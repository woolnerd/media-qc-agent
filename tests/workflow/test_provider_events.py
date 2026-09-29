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
from media_qc_agent.quality.environment import Environment, ScriptScene
from media_qc_agent.quality.spoken_text import SpokenTextCapabilities
from media_qc_agent.workflow.models import ProviderEventDisposition, VideoSources


class ProviderCompletionTests(unittest.TestCase):
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

    def submit_run(self, run_id: str) -> str:
        self.repository.create(
            sources=self.sources,
            run_id=run_id,
            finding=QualityFinding(
                kind=FailureKind.VISUAL_QUALITY,
                explanation="Synthetic jerky video",
                confidence=0.95,
            ),
            observed_artifact_version_id=self.observed_video_id,
        )
        self.repository.approve(
            run_id, plan_version_id=self.repository.get(run_id).plan_version_id
        )
        submitted = WorkflowExecutor(
            repository=self.repository, provider=self.provider
        ).submit(run_id)
        assert submitted.external_job_id is not None
        return submitted.external_job_id

    def test_duplicate_completion_returns_original_without_another_transition(
        self,
    ) -> None:
        external_job_id = self.submit_run("run-1")

        first = self.repository.record_completion(
            external_job_id=external_job_id,
            external_event_id="event-1",
        )
        changes_after_first = self.connection.total_changes
        restarted = WorkflowRepository(self.connection)
        second = restarted.record_completion(
            external_job_id=external_job_id,
            external_event_id="event-1",
        )

        self.assertEqual(first.status, WorkflowStatus.SUCCEEDED)
        self.assertEqual(second, first)
        self.assertEqual(self.connection.total_changes, changes_after_first)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM provider_events").fetchone()[
                0
            ],
            1,
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM provider_jobs").fetchone()[0],
            1,
        )
        job = restarted.get_provider_job(external_job_id)
        self.assertEqual(job.run_id, "run-1")
        self.assertEqual(job.idempotency_key, "workflow-run:run-1:regenerate_video")
        event = restarted.get_provider_event("event-1")
        self.assertEqual(event.external_job_id, external_job_id)
        self.assertEqual(event.result_status, WorkflowStatus.SUCCEEDED)
        self.assertEqual(event.disposition, ProviderEventDisposition.APPLIED)

    def test_external_event_id_cannot_be_reused_for_another_job(self) -> None:
        first_job_id = self.submit_run("run-1")
        second_job_id = self.submit_run("run-2")
        self.repository.record_completion(
            external_job_id=first_job_id,
            external_event_id="event-1",
        )

        with self.assertRaisesRegex(ValueError, "event identifier"):
            self.repository.record_completion(
                external_job_id=second_job_id,
                external_event_id="event-1",
            )

        self.assertEqual(self.repository.get("run-2").status, WorkflowStatus.SUBMITTED)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM provider_events").fetchone()[
                0
            ],
            1,
        )

    def test_second_completion_id_is_audited_without_repeating_transition(self) -> None:
        external_job_id = self.submit_run("run-1")
        first = self.repository.record_completion(
            external_job_id=external_job_id,
            external_event_id="event-1",
        )
        changes_after_first = self.connection.total_changes

        second = self.repository.record_completion(
            external_job_id=external_job_id,
            external_event_id="event-2",
        )

        self.assertEqual(second, first)
        self.assertEqual(self.connection.total_changes, changes_after_first + 1)
        self.assertEqual(
            self.repository.get_provider_event("event-2").disposition,
            ProviderEventDisposition.REDUNDANT,
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM provider_events").fetchone()[
                0
            ],
            2,
        )

    def test_external_job_id_cannot_be_reused_across_runs(self) -> None:
        external_job_id = self.submit_run("run-1")
        self.repository.create(
            sources=self.sources,
            run_id="run-2",
            finding=QualityFinding(
                kind=FailureKind.VISUAL_QUALITY,
                explanation="Another synthetic jerky video",
                confidence=0.95,
            ),
            observed_artifact_version_id=self.observed_video_id,
        )
        self.repository.approve(
            "run-2", plan_version_id=self.repository.get("run-2").plan_version_id
        )
        self.repository.reserve_submission(
            run_id="run-2",
            expected_plan_version_id=self.repository.get("run-2").plan_version_id,
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.record_submission(
                run_id="run-2",
                external_job_id=external_job_id,
                expected_plan_version_id=self.repository.get("run-2").plan_version_id,
            )

        self.assertEqual(self.repository.get("run-2").status, WorkflowStatus.SUBMITTING)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM provider_jobs").fetchone()[0],
            1,
        )

    def test_old_job_completion_is_recorded_without_advancing_new_retry(self) -> None:
        old_job_id = self.submit_run("run-1")
        awaiting_approval = self.repository.request_retry("run-1")
        self.assertEqual(awaiting_approval.status, WorkflowStatus.AWAITING_APPROVAL)
        self.repository.approve(
            "run-1", plan_version_id=self.repository.get("run-1").plan_version_id
        )
        new_job_id = (
            WorkflowExecutor(repository=self.repository, provider=self.provider)
            .submit("run-1")
            .external_job_id
        )
        assert new_job_id is not None
        self.assertNotEqual(new_job_id, old_job_id)

        result = self.repository.record_completion(
            external_job_id=old_job_id, external_event_id="late-old-job"
        )

        self.assertEqual(result.status, WorkflowStatus.SUBMITTED)
        self.assertEqual(result.external_job_id, new_job_id)
        event = self.repository.get_provider_event("late-old-job")
        self.assertEqual(event.disposition, ProviderEventDisposition.STALE)
        self.assertEqual(event.reason, "provider job is no longer active")
        self.assertEqual(
            self.repository.record_completion(
                external_job_id=new_job_id, external_event_id="new-job-completed"
            ).status,
            WorkflowStatus.SUCCEEDED,
        )

    def test_old_completion_during_retry_approval_is_stale(self) -> None:
        old_job_id = self.submit_run("run-1")
        self.repository.request_retry("run-1")

        result = self.repository.record_completion(
            external_job_id=old_job_id, external_event_id="late-during-approval"
        )

        self.assertEqual(result.status, WorkflowStatus.AWAITING_APPROVAL)
        self.assertEqual(
            self.repository.get_provider_event("late-during-approval").disposition,
            ProviderEventDisposition.STALE,
        )

    def test_retry_requires_fresh_approval_and_uses_unique_job_keys(self) -> None:
        first_job_id = self.submit_run("run-1")
        self.repository.request_retry("run-1")
        with self.assertRaisesRegex(ValueError, "approved"):
            WorkflowExecutor(repository=self.repository, provider=self.provider).submit(
                "run-1"
            )
        self.repository.approve(
            "run-1", plan_version_id=self.repository.get("run-1").plan_version_id
        )
        second_job_id = (
            WorkflowExecutor(repository=self.repository, provider=self.provider)
            .submit("run-1")
            .external_job_id
        )
        self.repository.request_retry("run-1")
        self.repository.approve(
            "run-1", plan_version_id=self.repository.get("run-1").plan_version_id
        )
        third_job_id = (
            WorkflowExecutor(repository=self.repository, provider=self.provider)
            .submit("run-1")
            .external_job_id
        )

        self.assertEqual(len({first_job_id, second_job_id, third_job_id}), 3)
        self.assertEqual(self.provider.jobs_created, 3)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM provider_jobs").fetchone()[0],
            3,
        )

    def test_impossible_active_job_transition_is_quarantined_with_reason(self) -> None:
        job_id = self.submit_run("run-1")
        self.connection.execute(
            "UPDATE workflow_runs SET status = ? WHERE id = ?",
            (WorkflowStatus.READY, "run-1"),
        )
        self.connection.commit()

        result = self.repository.record_completion(
            external_job_id=job_id, external_event_id="impossible-completion"
        )

        self.assertEqual(result.status, WorkflowStatus.READY)
        event = self.repository.get_provider_event("impossible-completion")
        self.assertEqual(event.disposition, ProviderEventDisposition.REJECTED)
        self.assertEqual(
            event.reason, "active provider job cannot complete while workflow is ready"
        )


if __name__ == "__main__":
    unittest.main()
