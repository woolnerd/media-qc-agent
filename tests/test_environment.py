import sqlite3
import unittest

from media_qc_agent import (
    ArtifactKind,
    FailureKind,
    FakeVideoProvider,
    QualityFinding,
    RepairAction,
    WorkflowExecutor,
    WorkflowRepository,
    WorkflowStatus,
)
from media_qc_agent.environment import (
    Environment,
    ScriptScene,
    check_script_avatar_compatibility,
)
from media_qc_agent.quality_records import EvidenceRole
from media_qc_agent.spoken_text import SpokenTextCapabilities
from media_qc_agent.workflow import VideoSources

LITERAL_MODEL = SpokenTextCapabilities("synthetic-tts", "literal-v1", frozenset())


class EnvironmentCheckTests(unittest.TestCase):
    def test_oven_script_and_office_avatar_produce_grounded_finding(self) -> None:
        result = check_script_avatar_compatibility(
            script_version_id="script-oven",
            authored_text="Bake the bread in the oven.",
            scene=ScriptScene(Environment.KITCHEN, evidence_phrase="oven"),
            avatar_version_id="avatar-office",
            avatar_environment=Environment.OFFICE,
        )

        assert result.finding is not None
        assert result.evidence is not None
        self.assertEqual(result.finding.kind, FailureKind.ENVIRONMENT_MISMATCH)
        self.assertEqual(result.evidence.script_version_id, "script-oven")
        self.assertEqual(result.evidence.avatar_version_id, "avatar-office")
        self.assertEqual(result.evidence.script_phrase, "oven")
        self.assertEqual(result.evidence.required_environment, Environment.KITCHEN)
        self.assertEqual(result.evidence.avatar_environment, Environment.OFFICE)
        self.assertIn("oven", result.finding.explanation)

    def test_matching_or_neutral_scene_passes(self) -> None:
        for avatar_environment in (Environment.KITCHEN, Environment.NEUTRAL):
            with self.subTest(avatar_environment=avatar_environment):
                result = check_script_avatar_compatibility(
                    script_version_id="script-oven",
                    authored_text="Bake in the oven.",
                    scene=ScriptScene(Environment.KITCHEN, evidence_phrase="oven"),
                    avatar_version_id="avatar-1",
                    avatar_environment=avatar_environment,
                )
                self.assertIsNone(result.finding)
                self.assertIsNone(result.evidence)


class EnvironmentWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.repository = WorkflowRepository(self.connection)
        self.repository.initialize()
        self.repository.create_script_version(
            version_id="script-oven",
            authored_text="Bake the bread in the oven.",
            scene=ScriptScene(Environment.KITCHEN, evidence_phrase="oven"),
        )
        self.repository.create_tts_input_version(
            version_id="tts-oven",
            script_version_id="script-oven",
            capabilities=LITERAL_MODEL,
        )
        self.repository.create_avatar_version(
            version_id="avatar-office", environment=Environment.OFFICE
        )
        self.repository.create_source_version(
            version_id="voice-1", kind=ArtifactKind.VOICE
        )
        self.sources = VideoSources(
            "script-oven", "tts-oven", "avatar-office", "voice-1"
        )
        self.observed_video_id = self.repository.create_synthetic_video_version(
            fixture_job_id="observed-fixture", sources=self.sources
        ).id
        self.provider = FakeVideoProvider()

    def tearDown(self) -> None:
        self.connection.close()

    def test_preflight_creates_human_choice_without_provider_job(self) -> None:
        run = self.repository.create_environment_run(
            run_id="run-oven", sources=self.sources
        )

        assert run is not None
        evidence = self.repository.get_quality_evidence(f"finding-{run.id}")
        self.assertEqual(evidence[0].role, EvidenceRole.FACT)
        self.assertEqual(evidence[0].artifact_version_id, "script-oven")
        self.assertEqual(evidence[1].artifact_version_id, "avatar-office")
        self.assertEqual(evidence[-1].role, EvidenceRole.UNCERTAINTY)
        self.assertEqual(run.status, WorkflowStatus.NEEDS_INPUT)
        assert run.clarification is not None
        self.assertEqual(
            {option.action for option in run.clarification.options},
            {RepairAction.REVISE_SCRIPT, RepairAction.CHANGE_AVATAR},
        )
        self.assertEqual(self.provider.jobs_created, 0)
        self.assertEqual(
            WorkflowRepository(self.connection).get_script_scene("script-oven"),
            ScriptScene(Environment.KITCHEN, evidence_phrase="oven"),
        )
        self.assertEqual(
            WorkflowRepository(self.connection).get_avatar_environment("avatar-office"),
            Environment.OFFICE,
        )

    def test_mismatched_sources_cannot_be_approved_for_video(self) -> None:
        self.repository.create(
            run_id="visual-run",
            finding=QualityFinding(
                kind=FailureKind.VISUAL_QUALITY,
                explanation="Synthetic visual defect",
                confidence=0.9,
            ),
            sources=self.sources,
            observed_artifact_version_id=self.observed_video_id,
        )

        with self.assertRaisesRegex(ValueError, "environment mismatch"):
            self.repository.approve(
                "visual-run",
                plan_version_id=self.repository.get("visual-run").plan_version_id,
            )
        with self.assertRaisesRegex(ValueError, "approved"):
            WorkflowExecutor(repository=self.repository, provider=self.provider).submit(
                "visual-run"
            )
        self.assertEqual(self.provider.jobs_created, 0)

    def test_avatar_choice_resolves_mismatch_before_approval(self) -> None:
        self.repository.create_environment_run(run_id="run-oven", sources=self.sources)
        self.repository.select_repair("run-oven", RepairAction.CHANGE_AVATAR)
        self.repository.create_avatar_version(
            version_id="avatar-kitchen", environment=Environment.KITCHEN
        )
        self.repository.bind_replacement("run-oven", "avatar-kitchen")

        approved = self.repository.approve(
            "run-oven", plan_version_id=self.repository.get("run-oven").plan_version_id
        )

        self.assertEqual(approved.status, WorkflowStatus.READY)
        self.assertEqual(approved.sources.avatar_version_id, "avatar-kitchen")

    def test_script_choice_needs_new_matching_script_and_tts_input(self) -> None:
        self.repository.create_environment_run(run_id="run-oven", sources=self.sources)
        self.repository.select_repair("run-oven", RepairAction.REVISE_SCRIPT)
        self.repository.create_script_version(
            version_id="script-office",
            authored_text="Review the report in the office.",
            scene=ScriptScene(Environment.OFFICE, evidence_phrase="office"),
        )
        self.repository.create_tts_input_version(
            version_id="tts-office",
            script_version_id="script-office",
            capabilities=LITERAL_MODEL,
        )
        self.repository.bind_replacement("run-oven", "script-office")
        self.repository.bind_tts_input("run-oven", "tts-office")

        approved = self.repository.approve(
            "run-oven", plan_version_id=self.repository.get("run-oven").plan_version_id
        )

        self.assertEqual(approved.status, WorkflowStatus.READY)
        self.assertEqual(approved.sources.script_version_id, "script-office")

    def test_scene_evidence_must_exist_in_script(self) -> None:
        with self.assertRaisesRegex(ValueError, "evidence phrase"):
            self.repository.create_script_version(
                version_id="script-bad",
                authored_text="A generic synthetic sentence.",
                scene=ScriptScene(Environment.KITCHEN, evidence_phrase="oven"),
            )
        with self.assertRaises(KeyError):
            self.repository.get_artifact_version("script-bad")

    def test_avatar_cannot_be_created_without_environment_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "create_avatar_version"):
            self.repository.create_source_version(
                version_id="avatar-missing", kind=ArtifactKind.AVATAR
            )


if __name__ == "__main__":
    unittest.main()
