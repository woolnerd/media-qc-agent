import sqlite3
import unittest

from media_qc_agent import (
    ArtifactKind,
    FailureKind,
    FakeVideoProvider,
    QualityFinding,
    WorkflowExecutor,
    WorkflowRepository,
)
from media_qc_agent.quality.environment import Environment, ScriptScene
from media_qc_agent.quality.spoken_text import (
    Notation,
    SpokenTextCapabilities,
    UnsafeSpokenText,
    prepare_spoken_text,
)
from media_qc_agent.workflow.models import VideoSources

LITERAL_MODEL = SpokenTextCapabilities(
    provider="synthetic-tts", model="literal-v1", supported_notation=frozenset()
)
NOTATION_AWARE_MODEL = SpokenTextCapabilities(
    provider="synthetic-tts",
    model="notation-aware-v1",
    supported_notation=frozenset(
        {Notation.TEMPERATURE, Notation.PERCENT, Notation.KILOMETER}
    ),
)


class SpokenTextGateTests(unittest.TestCase):
    def test_normalizes_unambiguous_temperature_units_and_percent(self) -> None:
        authored = "Heat to 450°F, carry 5 km, and set power to 50%."

        result = prepare_spoken_text(authored, LITERAL_MODEL)

        self.assertEqual(result.authored_text, authored)
        self.assertEqual(
            result.spoken_text,
            "Heat to 450 degrees Fahrenheit, carry 5 kilometers, "
            "and set power to 50 percent.",
        )
        self.assertEqual(result.issues, ())
        self.assertTrue(result.notation_compatible)
        self.assertIn("synthetic provider profiles", result.demo_notice)
        self.assertIn("does not verify pronunciation", result.demo_notice)

    def test_capability_profile_controls_which_notation_is_retained(self) -> None:
        authored = "Heat to 450°C, carry 5 km, and set power to 50%."

        result = prepare_spoken_text(authored, NOTATION_AWARE_MODEL)

        self.assertEqual(result.spoken_text, authored)
        self.assertEqual(result.issues, ())
        self.assertEqual(result.capabilities.model, "notation-aware-v1")

    def test_ambiguous_temperature_unit_and_symbol_are_reported(self) -> None:
        result = prepare_spoken_text("Heat to 450*F and ask R&D.", LITERAL_MODEL)

        self.assertIn("ambiguous_temperature", {issue.code for issue in result.issues})
        self.assertIn("unsupported_symbol", {issue.code for issue in result.issues})
        self.assertIn("450*F", {issue.token for issue in result.issues})
        self.assertFalse(result.notation_compatible)

    def test_ambiguous_unit_is_not_guessed(self) -> None:
        result = prepare_spoken_text("Move it 5m.", LITERAL_MODEL)

        self.assertEqual(result.spoken_text, "Move it 5m.")
        self.assertIn("ambiguous_unit", {issue.code for issue in result.issues})

    def test_unmarked_temperature_and_attached_percent_are_blocked(self) -> None:
        result = prepare_spoken_text(
            "Heat to 450F; add 50%off and 5lbs.", LITERAL_MODEL
        )

        self.assertIn("unsupported_unit", {issue.code for issue in result.issues})
        self.assertIn("unsupported_symbol", {issue.code for issue in result.issues})
        self.assertIn("5lbs", {issue.token for issue in result.issues})


class TtsInputVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.repository = WorkflowRepository(self.connection)
        self.repository.initialize()
        self.repository.artifacts.create_script_version(
            version_id="script-1",
            authored_text="Heat to 450°F.",
            scene=ScriptScene(Environment.NEUTRAL),
        )
        self.repository.artifacts.create_avatar_version(
            version_id="avatar-1", environment=Environment.NEUTRAL
        )
        self.repository.artifacts.create_voice_version("voice-1")

    def tearDown(self) -> None:
        self.connection.close()

    def test_accepted_input_retains_script_and_profile_lineage(self) -> None:
        self.repository.artifacts.create_tts_input_version(
            version_id="tts-1",
            script_version_id="script-1",
            capabilities=LITERAL_MODEL,
        )

        artifact = self.repository.artifacts.get("tts-1")
        spoken = self.repository.artifacts.tts_input("tts-1")
        self.assertEqual(
            self.repository.artifacts.script_text("script-1"), "Heat to 450°F."
        )
        self.assertEqual(artifact.source_versions, ((ArtifactKind.SCRIPT, "script-1"),))
        self.assertEqual(spoken.authored_text, "Heat to 450°F.")
        self.assertEqual(spoken.spoken_text, "Heat to 450 degrees Fahrenheit.")
        self.assertEqual(spoken.capabilities, LITERAL_MODEL)
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.artifacts.create_script_version(
                version_id="script-1",
                authored_text="Changed text.",
                scene=ScriptScene(Environment.NEUTRAL),
            )
        self.assertEqual(
            self.repository.artifacts.script_text("script-1"), "Heat to 450°F."
        )

    def test_voice_creation_cannot_bypass_text_gate(self) -> None:
        with self.assertRaisesRegex(ValueError, "voice version ID"):
            self.repository.artifacts.create_voice_version("tts-1")

    def test_explicit_candidate_preserves_ambiguous_authored_text(self) -> None:
        self.repository.artifacts.create_script_version(
            version_id="script-2",
            authored_text="Heat to 450*F.",
            scene=ScriptScene(Environment.NEUTRAL),
        )
        self.repository.artifacts.create_tts_input_version(
            version_id="tts-1",
            script_version_id="script-2",
            candidate_text="Heat to 450 degrees Fahrenheit.",
            capabilities=LITERAL_MODEL,
        )

        spoken = self.repository.artifacts.tts_input("tts-1")
        self.assertEqual(spoken.authored_text, "Heat to 450*F.")
        self.assertEqual(spoken.candidate_text, "Heat to 450 degrees Fahrenheit.")
        self.assertEqual(spoken.spoken_text, spoken.candidate_text)

    def test_unsafe_replacement_never_reaches_provider(self) -> None:
        self.repository.artifacts.create_tts_input_version(
            version_id="tts-1",
            script_version_id="script-1",
            capabilities=LITERAL_MODEL,
        )
        self.repository.create(
            run_id="run-1",
            finding=QualityFinding(
                kind=FailureKind.TTS_INPUT_COMPATIBILITY,
                explanation="Earlier provider-facing text was unsafe",
                confidence=0.9,
            ),
            sources=VideoSources("script-1", "tts-1", "avatar-1", "voice-1"),
        )
        provider = FakeVideoProvider()

        with self.assertRaises(UnsafeSpokenText) as failure:
            self.repository.artifacts.create_tts_input_version(
                version_id="tts-2",
                script_version_id="script-1",
                candidate_text="Heat to 450*F.",
                capabilities=LITERAL_MODEL,
            )

        self.assertIn("450*F", {issue.token for issue in failure.exception.issues})
        with self.assertRaises(KeyError):
            self.repository.artifacts.get("tts-2")
        with self.assertRaises(KeyError):
            self.repository.bind_replacement(
                "run-1",
                "tts-2",
                expected_plan_version_id=self.repository.get("run-1").plan_version_id,
            )
        with self.assertRaisesRegex(ValueError, "not awaiting approval"):
            self.repository.approve(
                "run-1", plan_version_id=self.repository.get("run-1").plan_version_id
            )
        with self.assertRaisesRegex(ValueError, "approved"):
            WorkflowExecutor(repository=self.repository, provider=provider).submit(
                "run-1"
            )
        self.assertEqual(provider.jobs_created, 0)

    def test_run_cannot_start_with_tts_input_from_another_script(self) -> None:
        self.repository.artifacts.create_script_version(
            version_id="script-2",
            authored_text="Another synthetic sentence.",
            scene=ScriptScene(Environment.NEUTRAL),
        )
        self.repository.artifacts.create_tts_input_version(
            version_id="tts-1",
            script_version_id="script-1",
            capabilities=LITERAL_MODEL,
        )

        with self.assertRaisesRegex(ValueError, "TTS input"):
            self.repository.create(
                run_id="run-1",
                finding=QualityFinding(
                    kind=FailureKind.VISUAL_QUALITY,
                    explanation="Synthetic visual defect",
                    confidence=0.9,
                ),
                sources=VideoSources("script-2", "tts-1", "avatar-1", "voice-1"),
            )


if __name__ == "__main__":
    unittest.main()
