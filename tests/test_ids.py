import sqlite3
import unittest

from media_qc_agent import ArtifactKind, FailureKind, QualityFinding, WorkflowRepository
from media_qc_agent.environment import Environment, ScriptScene
from media_qc_agent.ids import (
    validate_artifact_version_id,
    validate_external_id,
    validate_run_id,
    video_version_id,
)
from media_qc_agent.spoken_text import SpokenTextCapabilities
from media_qc_agent.workflow import VideoSources


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
                repository.create_script_version(
                    version_id="avatar-2",
                    authored_text="A synthetic sentence.",
                    scene=ScriptScene(Environment.NEUTRAL),
                )
            sources = VideoSources("script-1", "tts-1", "avatar-1", "voice-1")
            repository.create_script_version(
                version_id="script-1",
                authored_text="A synthetic sentence.",
                scene=ScriptScene(Environment.NEUTRAL),
            )
            repository.create_avatar_version(
                version_id="avatar-1", environment=Environment.NEUTRAL
            )
            for kind, version_id in sources.dependencies():
                if kind in {
                    ArtifactKind.SCRIPT,
                    ArtifactKind.TTS_INPUT,
                    ArtifactKind.AVATAR,
                }:
                    continue
                repository.create_source_version(version_id=version_id, kind=kind)
            repository.create_tts_input_version(
                version_id="tts-1",
                script_version_id="script-1",
                capabilities=SpokenTextCapabilities(
                    "synthetic-tts", "literal-v1", frozenset()
                ),
            )
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
            )
            repository.approve("run-1")
            with self.assertRaisesRegex(ValueError, "provider job"):
                repository.record_submission(run_id="run-1", external_job_id=" ")
            with self.assertRaisesRegex(ValueError, "provider event"):
                repository.record_completion(
                    external_job_id="job-1", external_event_id=" "
                )
            with self.assertRaisesRegex(ValueError, "caption"):
                repository.record_caption_version(
                    version_id="video:job-1", video_version_id="video:job-1"
                )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
