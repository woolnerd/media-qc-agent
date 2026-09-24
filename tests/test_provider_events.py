import sqlite3
import unittest

from media_qc_agent import (
    FailureKind,
    FakeVideoProvider,
    QualityFinding,
    WorkflowExecutor,
    WorkflowRepository,
    WorkflowStatus,
)


class ProviderCompletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.repository = WorkflowRepository(self.connection)
        self.repository.initialize()
        self.provider = FakeVideoProvider()

    def tearDown(self) -> None:
        self.connection.close()

    def submit_run(self, run_id: str) -> str:
        self.repository.create(
            run_id=run_id,
            finding=QualityFinding(
                kind=FailureKind.VISUAL_QUALITY,
                explanation="Synthetic jerky video",
                confidence=0.95,
            ),
        )
        self.repository.approve(run_id)
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
            self.connection.execute("SELECT COUNT(*) FROM provider_events").fetchone()[
                0
            ],
            2,
        )

    def test_external_job_id_cannot_be_reused_across_runs(self) -> None:
        external_job_id = self.submit_run("run-1")
        self.repository.create(
            run_id="run-2",
            finding=QualityFinding(
                kind=FailureKind.VISUAL_QUALITY,
                explanation="Another synthetic jerky video",
                confidence=0.95,
            ),
        )
        self.repository.approve("run-2")

        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.record_submission(
                run_id="run-2", external_job_id=external_job_id
            )

        self.assertEqual(self.repository.get("run-2").status, WorkflowStatus.READY)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM provider_jobs").fetchone()[0],
            1,
        )


if __name__ == "__main__":
    unittest.main()
