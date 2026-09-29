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
from media_qc_agent.quality.environment import Environment, ScriptScene
from media_qc_agent.quality.spoken_text import SpokenTextCapabilities
from media_qc_agent.workflow.models import VideoSources


class ArtifactLineageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.repository = WorkflowRepository(self.connection)
        self.repository.initialize()
        self.provider = FakeVideoProvider()
        self.sources = VideoSources("script-1", "tts-1", "avatar-1", "voice-1")
        self.repository.create_script_version(
            version_id="script-1",
            authored_text="A synthetic sentence.",
            scene=ScriptScene(Environment.NEUTRAL),
        )
        self.repository.create_avatar_version(
            version_id="avatar-1", environment=Environment.NEUTRAL
        )
        for version_id, kind in (("voice-1", ArtifactKind.VOICE),):
            self.repository.create_source_version(version_id=version_id, kind=kind)
        self.repository.create_tts_input_version(
            version_id="tts-1",
            script_version_id="script-1",
            capabilities=SpokenTextCapabilities(
                "synthetic-tts", "literal-v1", frozenset()
            ),
        )
        self.observed_video_id = self.repository.create_synthetic_video_version(
            fixture_job_id="observed-fixture", sources=self.sources
        ).id

    def tearDown(self) -> None:
        self.connection.close()

    def create_run(self, run_id: str, kind: FailureKind) -> None:
        self.repository.create(
            run_id=run_id,
            finding=QualityFinding(
                kind=kind, explanation="synthetic defect", confidence=0.9
            ),
            sources=self.sources,
            observed_artifact_version_id=(
                self.observed_video_id if kind is FailureKind.VISUAL_QUALITY else None
            ),
        )

    def submit(self, run_id: str) -> str:
        self.repository.approve(
            run_id, plan_version_id=self.repository.get(run_id).plan_version_id
        )
        job_id = (
            WorkflowExecutor(repository=self.repository, provider=self.provider)
            .submit(run_id)
            .external_job_id
        )
        assert job_id is not None
        return job_id

    def test_replacement_video_preserves_prior_version_and_exact_sources(self) -> None:
        self.create_run("run-1", FailureKind.VISUAL_QUALITY)
        first_job = self.submit("run-1")
        self.repository.record_completion(
            external_job_id=first_job, external_event_id="event-1"
        )
        first_video = self.repository.get("run-1").active_video_version_id
        assert first_video is not None
        prior_caption = self.repository.record_caption_version(
            version_id="caption-1", video_version_id=first_video
        )

        self.repository.request_retry("run-1")
        second_job = self.submit("run-1")
        self.repository.record_completion(
            external_job_id=second_job, external_event_id="event-2"
        )
        second_video = self.repository.get("run-1").active_video_version_id
        assert second_video is not None

        self.assertNotEqual(first_video, second_video)
        self.assertEqual(
            self.repository.get_artifact_version(first_video).source_versions,
            self.sources.dependencies(),
        )
        self.assertEqual(
            self.repository.get_artifact_version(second_video).source_versions,
            self.sources.dependencies(),
        )
        self.assertEqual(
            self.repository.get_artifact_version(first_video).external_job_id,
            first_job,
        )
        self.assertEqual(
            prior_caption.source_versions,
            ((ArtifactKind.VIDEO, first_video),),
        )

    def test_caption_lineage_identifies_exact_video_version(self) -> None:
        self.create_run("run-1", FailureKind.VISUAL_QUALITY)
        job_id = self.submit("run-1")
        self.repository.record_completion(
            external_job_id=job_id, external_event_id="event-1"
        )
        video_id = self.repository.get("run-1").active_video_version_id
        assert video_id is not None

        caption = self.repository.record_caption_version(
            version_id="caption-1", video_version_id=video_id
        )

        self.assertEqual(caption.kind, ArtifactKind.CAPTIONS)
        self.assertEqual(caption.source_versions, ((ArtifactKind.VIDEO, video_id),))
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.record_caption_version(
                version_id="caption-1", video_version_id=video_id
            )

    def test_selected_script_repair_requires_exact_replacement_before_approval(
        self,
    ) -> None:
        self.create_run("run-1", FailureKind.ENVIRONMENT_MISMATCH)
        self.repository.select_repair("run-1", RepairAction.REVISE_SCRIPT)
        self.repository.create_script_version(
            version_id="script-2",
            authored_text="A revised synthetic sentence.",
            scene=ScriptScene(Environment.NEUTRAL),
        )

        bound = self.repository.bind_replacement("run-1", "script-2")

        self.assertEqual(bound.status, WorkflowStatus.AWAITING_APPROVAL)
        self.assertEqual(bound.sources.script_version_id, "script-2")
        with self.assertRaisesRegex(ValueError, "TTS input"):
            self.repository.approve(
                "run-1", plan_version_id=self.repository.get("run-1").plan_version_id
            )
        self.repository.create_tts_input_version(
            version_id="tts-2",
            script_version_id="script-2",
            capabilities=SpokenTextCapabilities(
                "synthetic-tts", "literal-v1", frozenset()
            ),
        )
        self.repository.bind_tts_input("run-1", "tts-2")
        job_id = self.submit("run-1")
        self.repository.record_completion(
            external_job_id=job_id, external_event_id="event-1"
        )
        video_id = self.repository.get("run-1").active_video_version_id
        assert video_id is not None
        self.assertIn(
            (ArtifactKind.SCRIPT, "script-2"),
            self.repository.get_artifact_version(video_id).source_versions,
        )
        self.assertIn(
            (ArtifactKind.TTS_INPUT, "tts-2"),
            self.repository.get_artifact_version(video_id).source_versions,
        )

    def test_wrong_kind_replacement_stays_blocked(self) -> None:
        self.create_run("run-1", FailureKind.ENVIRONMENT_MISMATCH)
        self.repository.select_repair("run-1", RepairAction.CHANGE_AVATAR)
        with self.assertRaisesRegex(ValueError, "avatar"):
            self.repository.bind_replacement("run-1", "script-1")
        self.assertEqual(
            self.repository.get("run-1").status, WorkflowStatus.NEEDS_REPAIR_INPUT
        )

    def test_avatar_replacement_binds_selected_version(self) -> None:
        self.create_run("run-1", FailureKind.ENVIRONMENT_MISMATCH)
        self.repository.select_repair("run-1", RepairAction.CHANGE_AVATAR)
        self.repository.create_avatar_version(
            version_id="avatar-2", environment=Environment.NEUTRAL
        )

        bound = self.repository.bind_replacement("run-1", "avatar-2")

        self.assertEqual(bound.status, WorkflowStatus.AWAITING_APPROVAL)
        self.assertEqual(bound.sources.avatar_version_id, "avatar-2")
        self.assertEqual(bound.sources.script_version_id, "script-1")

    def test_stale_completion_cannot_create_or_promote_video(self) -> None:
        self.create_run("run-1", FailureKind.VISUAL_QUALITY)
        old_job = self.submit("run-1")
        self.repository.request_retry("run-1")
        current_job = self.submit("run-1")

        self.repository.record_completion(
            external_job_id=old_job, external_event_id="stale-event"
        )

        self.assertIsNone(self.repository.get("run-1").active_video_version_id)
        with self.assertRaises(KeyError):
            self.repository.get_artifact_version(f"video:{old_job}")
        self.repository.record_completion(
            external_job_id=current_job, external_event_id="current-event"
        )
        self.assertEqual(
            self.repository.get("run-1").active_video_version_id,
            f"video:{current_job}",
        )

    def test_source_version_identity_is_immutable(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.create_script_version(
                version_id="script-1",
                authored_text="Another sentence.",
                scene=ScriptScene(Environment.NEUTRAL),
            )


if __name__ == "__main__":
    unittest.main()
