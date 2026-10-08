import sqlite3
import unittest

from media_qc_agent.api.scenarios import (
    SCENARIOS,
    FixtureDrift,
    create_scenario_run,
    seed_scenarios,
)
from media_qc_agent.domain.evidence import EvidenceRole
from media_qc_agent.domain.models import ArtifactKind
from media_qc_agent.quality.environment import Environment, ScriptScene
from media_qc_agent.workflow.repository import WorkflowRepository


class ScenarioFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.repository = WorkflowRepository(self.connection)
        self.repository.initialize()
        seed_scenarios(self.repository)

    def run_for(self, scenario_id: str) -> str:
        create_scenario_run(self.repository, scenario_id, scenario_id)
        return scenario_id

    def facts(self, run_id: str) -> tuple[tuple[str, str | None], ...]:
        finding = self.repository.get_quality_finding(run_id)
        return tuple(
            (item.statement, item.observed)
            for item in self.repository.get_quality_evidence(finding.id)
            if item.role is EvidenceRole.FACT
        )

    def test_weak_script_evidence_quotes_the_observed_script(self) -> None:
        run_id = self.run_for("weak-script")
        observed = self.repository.get_quality_finding(run_id).artifact_version_id
        script = self.repository.get_script_text(observed)
        quoted = [value for _, value in self.facts(run_id) if value]
        self.assertTrue(quoted)
        for value in quoted:
            self.assertIn(value, script)
        revised = self.repository.get_script_text("script-api-revised")
        self.assertNotIn(quoted[0], revised)

    def test_tts_scenario_grounds_rejection_and_normalization_in_the_gate(
        self,
    ) -> None:
        run_id = self.run_for("tts-input")
        run = self.repository.get(run_id)
        authored = self.repository.get_script_text(run.sources.script_version_id)
        self.assertIn("450*F", authored)
        observed = self.repository.get_tts_input_version(
            run.sources.tts_input_version_id
        )
        self.assertIn("450°F", observed.spoken_text)
        self.assertTrue(
            any(
                "ambiguous_temperature" in statement and observed == "450*F"
                for statement, observed in self.facts(run_id)
            )
        )

        replacement = self.repository.get_tts_input_version("tts-api-replacement")
        self.assertEqual(replacement.script_version_id, run.sources.script_version_id)
        self.assertIn("450 degrees Fahrenheit", replacement.spoken_text)
        self.assertEqual(replacement.authored_text, authored)

    def test_caption_scenario_captions_the_video_script(self) -> None:
        run_id = self.run_for("caption-format")
        caption_id = self.repository.get_quality_finding(run_id).artifact_version_id
        video = self.repository.get_artifact_version(
            dict(self.repository.get_artifact_version(caption_id).source_versions)[
                ArtifactKind.VIDEO
            ]
        )
        script = self.repository.get_script_text(
            dict(video.source_versions)[ArtifactKind.SCRIPT]
        )
        for cue in self.repository.get_caption_cues(caption_id):
            for line in cue.text.splitlines():
                self.assertIn(line, script)

    def test_observed_video_renders_the_approved_revised_script(self) -> None:
        video = self.repository.get_artifact_version("video:api-observed")
        self.assertEqual(
            dict(video.source_versions)[ArtifactKind.SCRIPT], "script-api-revised"
        )

    def test_every_scenario_creates_a_run(self) -> None:
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario.id):
                self.assertEqual(self.run_for(scenario.id), scenario.id)

    def test_reseeding_is_idempotent(self) -> None:
        seed_scenarios(self.repository)

    def test_tts_evidence_labels_reviewer_claims_as_inference(self) -> None:
        run_id = self.run_for("tts-input")
        finding = self.repository.get_quality_finding(run_id)
        evidence = self.repository.get_quality_evidence(finding.id)
        stored = self.repository.get_tts_input_version("tts-api-oven")
        facts = [item for item in evidence if item.role is EvidenceRole.FACT]
        self.assertIn(stored.spoken_text, [item.observed for item in facts])
        for item in facts:
            with self.subTest(statement=item.statement):
                self.assertNotIn("provider", item.statement.lower())
                self.assertNotIn("four fifty", item.statement.lower())
        self.assertTrue(
            any(
                "four fifty" in item.statement
                for item in evidence
                if item.role is EvidenceRole.INFERENCE
            )
        )

    def test_jerky_video_reports_two_spikes_as_four_changes(self) -> None:
        run_id = self.run_for("jerky-video")
        finding = self.repository.get_quality_finding(run_id)
        observed = [
            item.statement
            for item in self.repository.get_quality_evidence(finding.id)
            if item.role is EvidenceRole.FACT
        ]
        self.assertEqual(
            observed,
            [
                f"Motion jump from frame {start} to {start + 1}."
                for start in (3, 4, 7, 8)
            ],
        )

    def test_unknown_scenario_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            create_scenario_run(self.repository, "no-such-scenario", "run-x")


class FixtureDriftTests(unittest.TestCase):
    def test_stale_fixture_content_fails_with_a_reset_instruction(self) -> None:
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        repository = WorkflowRepository(connection)
        repository.initialize()
        repository.create_script_version(
            version_id="script-api-original",
            authored_text="A synthetic sentence.",
            scene=ScriptScene(Environment.NEUTRAL),
        )
        with self.assertRaisesRegex(FixtureDrift, "script-api-original.*delete"):
            seed_scenarios(repository)

    def test_each_fixture_kind_detects_stale_content(self) -> None:
        for version_id, statement in (
            (
                "script-api-kitchen",
                (
                    "UPDATE script_versions SET environment = 'neutral', "
                    "evidence_phrase = NULL WHERE version_id = 'script-api-kitchen'"
                ),
            ),
            (
                "avatar-api-kitchen",
                (
                    "UPDATE avatar_versions SET environment = 'office' "
                    "WHERE version_id = 'avatar-api-kitchen'"
                ),
            ),
            (
                "tts-api-replacement",
                (
                    "UPDATE tts_input_versions SET spoken_text = 'stale' "
                    "WHERE version_id = 'tts-api-replacement'"
                ),
            ),
            (
                "video:api-observed",
                (
                    "UPDATE artifact_dependencies SET source_id = 'script-api-original' "
                    "WHERE artifact_id = 'video:api-observed' AND source_kind = 'script'"
                ),
            ),
            (
                "caption-api-observed",
                (
                    "UPDATE caption_contents SET cues_json = '[]' "
                    "WHERE version_id = 'caption-api-observed'"
                ),
            ),
        ):
            with self.subTest(version_id=version_id):
                connection = sqlite3.connect(":memory:")
                self.addCleanup(connection.close)
                repository = WorkflowRepository(connection)
                repository.initialize()
                seed_scenarios(repository)
                with connection:
                    connection.execute(statement)
                with self.assertRaisesRegex(FixtureDrift, version_id):
                    seed_scenarios(repository)

    def test_drift_is_detected_before_any_fixture_is_created(self) -> None:
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        repository = WorkflowRepository(connection)
        repository.initialize()
        repository.create_avatar_version(
            version_id="avatar-api-office", environment=Environment.KITCHEN
        )
        with self.assertRaises(FixtureDrift):
            seed_scenarios(repository)
        with self.assertRaises(KeyError):
            repository.get_script_text("script-api-original")


if __name__ == "__main__":
    unittest.main()
