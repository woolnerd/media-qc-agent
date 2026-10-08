import sqlite3
import unittest

from media_qc_agent import ArtifactKind, FailureKind, QualityFinding, WorkflowRepository
from media_qc_agent.domain.ids import (
    artifact_kind,
    validate_artifact_version_id,
    validate_external_id,
    validate_run_id,
    video_version_id,
)
from media_qc_agent.quality.environment import Environment, ScriptScene
from media_qc_agent.quality.spoken_text import SpokenTextCapabilities
from media_qc_agent.workflow.models import VideoSources


class IdContractTests(unittest.TestCase):
    def test_artifact_ids_use_kind_prefixes(self) -> None:
        for kind, version_id in (
            (ArtifactKind.SCRIPT, "script-1"),
            (ArtifactKind.TTS_INPUT, "tts-1"),
            (ArtifactKind.AVATAR, "avatar-1"),
            (ArtifactKind.VOICE, "voice-1"),
            (ArtifactKind.CAPTIONS, "caption-1"),
        ):
            validate_artifact_version_id(version_id, kind)

        with self.assertRaisesRegex(ValueError, "script"):
            validate_artifact_version_id("avatar-1", ArtifactKind.SCRIPT)
        with self.assertRaisesRegex(ValueError, "caption"):
            validate_artifact_version_id("video:job-1", ArtifactKind.CAPTIONS)
        with self.assertRaisesRegex(ValueError, "generated"):
            validate_artifact_version_id("video:job-1", ArtifactKind.VIDEO)

    def test_artifact_kind_is_read_from_the_version_prefix(self) -> None:
        for version_id, kind in (
            ("script-1", ArtifactKind.SCRIPT),
            ("tts-benchmark", ArtifactKind.TTS_INPUT),
            ("avatar-1", ArtifactKind.AVATAR),
            ("voice-1", ArtifactKind.VOICE),
            ("caption-1", ArtifactKind.CAPTIONS),
            ("video:provider/job:42", ArtifactKind.VIDEO),
        ):
            with self.subTest(version_id=version_id):
                self.assertIs(artifact_kind(version_id), kind)
        for unknown in (
            "video-1",
            "video:",
            "video: ",
            "captions-1",
            "script-",
            "Script-1",
            "",
        ):
            with self.subTest(unknown=unknown):
                self.assertIsNone(artifact_kind(unknown))

    def test_run_ids_are_slugs_and_provider_ids_are_opaque(self) -> None:
        validate_run_id("demo-run")
        validate_external_id("provider/job:42", "provider job")
        self.assertEqual(video_version_id("provider/job:42"), "video:provider/job:42")
        for invalid in ("", "with space", "run:1", "-run", "Run-1"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_run_id(invalid)
        with self.assertRaisesRegex(ValueError, "provider job"):
            validate_external_id("  ", "provider job")

    def test_repository_rejects_wrong_source_prefix_and_run_id(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            repository = WorkflowRepository(connection)
            repository.initialize()
            with self.assertRaisesRegex(ValueError, "script"):
                repository.artifacts.create_script_version(
                    version_id="avatar-2",
                    authored_text="A synthetic sentence.",
                    scene=ScriptScene(Environment.NEUTRAL),
                )
            sources = VideoSources("script-1", "tts-1", "avatar-1", "voice-1")
            repository.artifacts.create_script_version(
                version_id="script-1",
                authored_text="A synthetic sentence.",
                scene=ScriptScene(Environment.NEUTRAL),
            )
            repository.artifacts.create_avatar_version(
                version_id="avatar-1", environment=Environment.NEUTRAL
            )
            repository.artifacts.create_voice_version(sources.voice_version_id)
            repository.artifacts.create_tts_input_version(
                version_id="tts-1",
                script_version_id="script-1",
                capabilities=SpokenTextCapabilities(
                    "synthetic-tts", "literal-v1", frozenset()
                ),
            )
            observed_video_id = repository.artifacts.create_synthetic_video_version(
                fixture_job_id="observed-fixture", sources=sources
            ).id
            with self.assertRaisesRegex(ValueError, "run ID"):
                repository.create(
                    run_id="bad run",
                    finding=QualityFinding(
                        kind=FailureKind.VISUAL_QUALITY,
                        explanation="synthetic defect",
                        confidence=0.9,
                    ),
                    sources=sources,
                )
            repository.create(
                run_id="run-1",
                finding=QualityFinding(
                    kind=FailureKind.VISUAL_QUALITY,
                    explanation="synthetic defect",
                    confidence=0.9,
                ),
                sources=sources,
                observed_artifact_version_id=observed_video_id,
            )
            repository.approve(
                "run-1", plan_version_id=repository.get("run-1").plan_version_id
            )
            with self.assertRaisesRegex(ValueError, "provider job"):
                repository.record_submission(
                    run_id="run-1",
                    external_job_id=" ",
                    expected_plan_version_id=repository.get("run-1").plan_version_id,
                )
            with self.assertRaisesRegex(ValueError, "provider event"):
                repository.record_completion(
                    external_job_id="job-1", external_event_id=" "
                )
            with self.assertRaisesRegex(ValueError, "caption"):
                repository.artifacts.create_caption_version(
                    version_id="video:job-1", video_version_id="video:job-1"
                )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
