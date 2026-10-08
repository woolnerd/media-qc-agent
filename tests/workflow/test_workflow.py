import sqlite3
import unittest

from media_qc_agent import (
    ArtifactKind,
    ClarificationRequest,
    FailureKind,
    FakeVideoProvider,
    QualityFinding,
    RepairAction,
    SimulatedProcessCrash,
    WorkflowExecutor,
    WorkflowRepository,
    WorkflowStatus,
)
from media_qc_agent.quality.environment import Environment, ScriptScene
from media_qc_agent.quality.spoken_text import SpokenTextCapabilities
from media_qc_agent.workflow.models import VideoSources


class WorkflowIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.repository = WorkflowRepository(self.connection)
        self.repository.initialize()
        self.provider = FakeVideoProvider()
        self.sources = VideoSources("script-1", "tts-1", "avatar-1", "voice-1")
        self.repository.artifacts.create_script_version(
            version_id="script-1",
            authored_text="A synthetic sentence.",
            scene=ScriptScene(Environment.NEUTRAL),
        )
        self.repository.artifacts.create_avatar_version(
            version_id="avatar-1", environment=Environment.NEUTRAL
        )
        self.repository.artifacts.create_voice_version("voice-1")
        self.repository.artifacts.create_tts_input_version(
            version_id="tts-1",
            script_version_id="script-1",
            capabilities=SpokenTextCapabilities(
                "synthetic-tts", "literal-v1", frozenset()
            ),
        )
        self.observed_video_id = (
            self.repository.artifacts.create_synthetic_video_version(
                fixture_job_id="observed-fixture", sources=self.sources
            ).id
        )

    def tearDown(self) -> None:
        self.connection.close()

    def create_visual_quality_run(self, run_id: str = "run-1") -> None:
        self.repository.create(
            sources=self.sources,
            run_id=run_id,
            finding=QualityFinding(
                kind=FailureKind.VISUAL_QUALITY,
                explanation="Generated video is jerky and unnatural",
                confidence=0.96,
            ),
            observed_artifact_version_id=self.observed_video_id,
        )

    def test_requires_approval_before_external_submission(self) -> None:
        self.create_visual_quality_run()
        executor = WorkflowExecutor(
            repository=self.repository,
            provider=self.provider,
        )

        with self.assertRaisesRegex(ValueError, "approved"):
            executor.submit("run-1")

        self.assertEqual(self.provider.jobs_created, 0)

    def test_tts_input_repair_waits_for_replacement_text_before_approval(self) -> None:
        run = self.repository.create(
            sources=self.sources,
            run_id="tts-input-1",
            finding=QualityFinding(
                kind=FailureKind.TTS_INPUT_COMPATIBILITY,
                explanation="The voice reads 450*F as four hundred fifty star F",
                confidence=0.98,
            ),
        )

        self.assertEqual(run.status, WorkflowStatus.NEEDS_REPAIR_INPUT)
        self.assertIsNone(run.clarification)
        assert run.plan is not None
        self.assertEqual(run.plan.action, RepairAction.REPAIR_TTS_INPUT)
        self.assertIsNone(run.idempotency_key)
        with self.assertRaisesRegex(ValueError, "not awaiting approval"):
            self.repository.approve(
                "tts-input-1",
                plan_version_id=self.repository.get("tts-input-1").plan_version_id,
            )
        with self.assertRaisesRegex(ValueError, "approved"):
            WorkflowExecutor(repository=self.repository, provider=self.provider).submit(
                "tts-input-1"
            )
        self.assertEqual(self.provider.jobs_created, 0)
        persisted = WorkflowRepository(self.connection).get("tts-input-1")
        self.assertEqual(persisted, run)

    def test_visual_retry_is_proposed_for_approval_not_launched(self) -> None:
        self.create_visual_quality_run()

        proposed = self.repository.get("run-1")

        self.assertEqual(proposed.status, WorkflowStatus.AWAITING_APPROVAL)
        assert proposed.plan is not None
        self.assertFalse(proposed.plan.requires_repair_input)
        self.assertEqual(self.provider.jobs_created, 0)

    def test_unresolved_human_choice_cannot_be_approved_as_executable_work(
        self,
    ) -> None:
        run = self.repository.create(
            sources=self.sources,
            run_id="mismatch-1",
            finding=QualityFinding(
                kind=FailureKind.ENVIRONMENT_MISMATCH,
                explanation="An oven script was assigned to the office avatar",
                confidence=0.91,
            ),
        )

        self.assertEqual(run.status, WorkflowStatus.NEEDS_INPUT)
        self.assertIsNone(run.plan)
        self.assertIsInstance(run.clarification, ClarificationRequest)
        with self.assertRaisesRegex(ValueError, "not awaiting approval"):
            self.repository.approve(
                "mismatch-1",
                plan_version_id=self.repository.get("mismatch-1").plan_version_id,
            )

        with self.assertRaisesRegex(ValueError, "approved"):
            WorkflowExecutor(repository=self.repository, provider=self.provider).submit(
                "mismatch-1"
            )

        self.assertEqual(self.provider.jobs_created, 0)

    def test_human_selection_creates_plan_awaiting_replacement_artifact(self) -> None:
        self.repository.create(
            sources=self.sources,
            run_id="mismatch-script",
            finding=QualityFinding(
                kind=FailureKind.ENVIRONMENT_MISMATCH,
                explanation="An oven script was assigned to the office avatar",
                confidence=0.91,
            ),
        )

        selected = self.repository.select_repair(
            "mismatch-script", RepairAction.REVISE_SCRIPT
        )

        self.assertEqual(selected.status, WorkflowStatus.NEEDS_REPAIR_INPUT)
        self.assertIsNone(selected.clarification)
        assert selected.plan is not None
        self.assertEqual(selected.plan.action, RepairAction.REVISE_SCRIPT)
        self.assertEqual(
            selected.plan.invalidates,
            {
                ArtifactKind.SCRIPT,
                ArtifactKind.TTS_INPUT,
                ArtifactKind.VIDEO,
                ArtifactKind.CAPTIONS,
            },
        )
        self.assertTrue(selected.plan.requires_repair_input)
        self.assertIsNone(selected.idempotency_key)
        with self.assertRaisesRegex(ValueError, "not awaiting approval"):
            self.repository.approve(
                "mismatch-script",
                plan_version_id=self.repository.get("mismatch-script").plan_version_id,
            )
        with self.assertRaisesRegex(ValueError, "approved"):
            WorkflowExecutor(repository=self.repository, provider=self.provider).submit(
                "mismatch-script"
            )
        self.assertEqual(self.provider.jobs_created, 0)

    def test_direct_script_repair_also_waits_for_replacement_input(self) -> None:
        run = self.repository.create(
            sources=self.sources,
            run_id="script-1",
            finding=QualityFinding(
                kind=FailureKind.SCRIPT_QUALITY,
                explanation="The script is unclear",
                confidence=0.9,
            ),
        )

        self.assertEqual(run.status, WorkflowStatus.NEEDS_REPAIR_INPUT)
        self.assertIsNone(run.idempotency_key)

    def test_avatar_selection_preserves_script_and_rejects_other_actions(self) -> None:
        self.repository.create(
            sources=self.sources,
            run_id="mismatch-avatar",
            finding=QualityFinding(
                kind=FailureKind.ENVIRONMENT_MISMATCH,
                explanation="An oven script was assigned to the office avatar",
                confidence=0.91,
            ),
        )

        with self.assertRaisesRegex(ValueError, "not offered"):
            self.repository.select_repair(
                "mismatch-avatar", RepairAction.REGENERATE_VIDEO
            )
        selected = self.repository.select_repair(
            "mismatch-avatar", RepairAction.CHANGE_AVATAR
        )

        assert selected.plan is not None
        self.assertEqual(selected.plan.action, RepairAction.CHANGE_AVATAR)
        self.assertEqual(
            selected.plan.invalidates,
            {ArtifactKind.AVATAR, ArtifactKind.VIDEO, ArtifactKind.CAPTIONS},
        )
        with self.assertRaisesRegex(ValueError, "not awaiting clarification"):
            self.repository.select_repair("mismatch-avatar", RepairAction.REVISE_SCRIPT)

    def test_clarification_and_selected_plan_survive_repository_restart(self) -> None:
        self.repository.create(
            sources=self.sources,
            run_id="mismatch-restart",
            finding=QualityFinding(
                kind=FailureKind.ENVIRONMENT_MISMATCH,
                explanation="Kitchen script with office avatar",
                confidence=0.9,
            ),
        )

        restarted = WorkflowRepository(self.connection)
        unresolved = restarted.get("mismatch-restart")
        self.assertIsNone(unresolved.plan)
        assert unresolved.clarification is not None
        self.assertEqual(len(unresolved.clarification.options), 2)

        restarted.select_repair("mismatch-restart", RepairAction.CHANGE_AVATAR)
        selected = WorkflowRepository(self.connection).get("mismatch-restart")
        self.assertIsNone(selected.clarification)
        assert selected.plan is not None
        self.assertEqual(selected.plan.action, RepairAction.CHANGE_AVATAR)
        self.assertEqual(selected.status, WorkflowStatus.NEEDS_REPAIR_INPUT)

    def test_records_one_external_job_after_approval(self) -> None:
        self.create_visual_quality_run()
        self.repository.approve(
            "run-1", plan_version_id=self.repository.get("run-1").plan_version_id
        )
        executor = WorkflowExecutor(
            repository=self.repository,
            provider=self.provider,
        )

        submitted = executor.submit("run-1")

        self.assertEqual(submitted.status, WorkflowStatus.SUBMITTED)
        self.assertEqual(submitted.external_job_id, "video-job-1")
        self.assertEqual(self.provider.jobs_created, 1)

    def test_restart_reconciles_crash_gap_without_duplicate_provider_job(self) -> None:
        self.create_visual_quality_run()
        self.repository.approve(
            "run-1", plan_version_id=self.repository.get("run-1").plan_version_id
        )
        first_process = WorkflowExecutor(
            repository=self.repository,
            provider=self.provider,
        )

        with self.assertRaises(SimulatedProcessCrash):
            first_process.submit(
                "run-1",
                crash_after_provider_accepts=True,
            )

        self.assertEqual(
            self.repository.get("run-1").status,
            WorkflowStatus.SUBMITTING,
        )
        self.assertEqual(self.provider.jobs_created, 1)

        restarted_process = WorkflowExecutor(
            repository=self.repository,
            provider=self.provider,
        )
        submitted = restarted_process.submit("run-1")

        self.assertEqual(submitted.status, WorkflowStatus.SUBMITTED)
        self.assertEqual(submitted.external_job_id, "video-job-1")
        self.assertEqual(self.provider.submit_attempts, 2)
        self.assertEqual(self.provider.jobs_created, 1)

    def test_repeated_execution_after_submission_is_a_no_op(self) -> None:
        self.create_visual_quality_run()
        self.repository.approve(
            "run-1", plan_version_id=self.repository.get("run-1").plan_version_id
        )
        executor = WorkflowExecutor(
            repository=self.repository,
            provider=self.provider,
        )
        first_result = executor.submit("run-1")

        second_result = executor.submit("run-1")

        self.assertEqual(second_result, first_result)
        self.assertEqual(self.provider.submit_attempts, 1)
        self.assertEqual(self.provider.jobs_created, 1)


if __name__ == "__main__":
    unittest.main()
