import sqlite3
import unittest

from media_qc_agent import (
    ArtifactKind,
    FailureKind,
    FakeVideoProvider,
    QualityFinding,
    WorkflowExecutor,
    WorkflowRepository,
    WorkflowStatus,
)
from media_qc_agent.workflow import ProviderEventDisposition, VideoSources


class ProviderTransitionRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.repository = WorkflowRepository(self.connection)
        self.repository.initialize()
        self.provider = FakeVideoProvider()
        sources = VideoSources("script-1", "tts-1", "avatar-1", "voice-1")
        for kind, version_id in sources.dependencies():
            self.repository.create_source_version(version_id=version_id, kind=kind)
        self.repository.create(
            run_id="run-1",
            finding=QualityFinding(
                kind=FailureKind.VISUAL_QUALITY,
                explanation="synthetic jerky video",
                confidence=0.95,
            ),
            sources=sources,
        )

    def tearDown(self) -> None:
        self.connection.close()

    def submit(self) -> str:
        self.repository.approve("run-1")
        job_id = (
            WorkflowExecutor(repository=self.repository, provider=self.provider)
            .submit("run-1")
            .external_job_id
        )
        assert job_id is not None
        return job_id

    def test_new_completion_before_old_keeps_new_video_and_audits_old(self) -> None:
        old_job = self.submit()
        self.repository.request_retry("run-1")
        new_job = self.submit()

        self.repository.record_completion(
            external_job_id=new_job, external_event_id="new-completed"
        )
        changes_after_current = self.connection.total_changes
        self.repository.record_completion(
            external_job_id=old_job, external_event_id="old-completed"
        )

        run = self.repository.get("run-1")
        self.assertEqual(run.status, WorkflowStatus.SUCCEEDED)
        self.assertEqual(run.active_video_version_id, f"video:{new_job}")
        self.assertEqual(self.connection.total_changes, changes_after_current + 1)
        self.assertEqual(
            self.repository.get_provider_event("old-completed").disposition,
            ProviderEventDisposition.STALE,
        )
        with self.assertRaises(KeyError):
            self.repository.get_artifact_version(f"video:{old_job}")

    def test_duplicate_current_event_cannot_make_another_video(self) -> None:
        job_id = self.submit()
        self.repository.record_completion(
            external_job_id=job_id, external_event_id="event-1"
        )
        changes_after_first = self.connection.total_changes

        self.repository.record_completion(
            external_job_id=job_id, external_event_id="event-1"
        )

        self.assertEqual(self.connection.total_changes, changes_after_first)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM artifact_versions WHERE kind = ?",
                (ArtifactKind.VIDEO,),
            ).fetchone()[0],
            1,
        )

    def test_impossible_transition_records_reason_without_video(self) -> None:
        job_id = self.submit()
        self.connection.execute(
            "UPDATE workflow_runs SET status = ? WHERE id = ?",
            (WorkflowStatus.READY, "run-1"),
        )
        self.connection.commit()

        self.repository.record_completion(
            external_job_id=job_id, external_event_id="impossible"
        )

        event = self.repository.get_provider_event("impossible")
        self.assertEqual(event.disposition, ProviderEventDisposition.REJECTED)
        self.assertIn("ready", event.reason or "")
        self.assertIsNone(self.repository.get("run-1").active_video_version_id)


if __name__ == "__main__":
    unittest.main()
