"""Challenge timing, commit ordering, recovery accounting, and sink isolation."""

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from media_qc_agent.api.scenarios import create_scenario_run, seed_scenarios
from media_qc_agent.workflow.database import Database
from media_qc_agent.workflow.executor import SimulatedProcessCrash
from media_qc_agent.workflow.provider import FakeVideoProvider
from media_qc_agent.workflow.repository import WorkflowRepository
from media_qc_agent.workflow.telemetry import (
    EventKind,
    ExecutionEvent,
    ExecutionObserver,
    reference,
    stderr_event,
)
from media_qc_agent.workflow.worker import DurableWorker
from media_qc_agent.workflow.worker_models import SubmissionLease
from media_qc_agent.workflow.worker_queue import SubmissionQueue


class TelemetryTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Database(Path(directory.name) / "workflow.sqlite3")
        self.provider = FakeVideoProvider()
        self.now = 1000.0
        self.elapsed = 10.0
        self.events: list[ExecutionEvent] = []
        self.observer = ExecutionObserver(
            self.events.append, clock=lambda: self.elapsed
        )
        with self.database.repository() as repository:
            repository.initialize()
            seed_scenarios(repository)
            run = create_scenario_run(repository, "jerky-video", "run-1")
            repository.approve(run.id, plan_version_id=run.plan_version_id)

    def worker(self) -> DurableWorker:
        return DurableWorker(
            self.database,
            self.provider,
            owner="worker-a",
            clock=lambda: self.now,
            observer=self.observer,
        )

    def test_job_timing_survives_new_connection_and_late_duplicate(self) -> None:
        self.worker().run_once()
        with self.database.repository() as repository:
            pending = repository.get_job_timing("video-job-1")
            self.assertIsNone(pending.recorded_job_to_completion_seconds)
            repository.record_completion(
                external_job_id="video-job-1", external_event_id="timing-first"
            )
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE provider_jobs SET created_at = '2026-10-05T12:00:10Z'"
            )
            connection.execute(
                "UPDATE provider_events SET created_at = '2026-10-05T12:04:10Z'"
            )
            connection.commit()
        with self.database.repository() as repository:
            before = repository.get_job_timing("video-job-1")
            self.assertEqual(before.recorded_job_to_completion_seconds, 240)
            repository.record_completion(
                external_job_id="video-job-1", external_event_id="timing-late"
            )
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE provider_events SET created_at = '2026-10-05T12:09:10Z' WHERE external_event_id = 'timing-late'"
            )
            connection.commit()
        with self.database.repository() as repository:
            self.assertEqual(repository.get_job_timing("video-job-1"), before)

    def test_recovery_preserves_first_claim_timestamp(self) -> None:
        with self.assertRaises(SimulatedProcessCrash):
            self.worker().run_once(crash_after_provider_accepts=True)
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE worker_attempts SET first_claimed_at = '2026-10-05T12:00:07Z'"
            )
            connection.commit()
        self.now += 31
        self.worker().run_once()
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT first_claimed_at, attempts FROM worker_attempts"
            ).fetchone()
            self.assertEqual(tuple(row), ("2026-10-05T12:00:07Z", 2))

    def test_recovery_records_two_calls_but_one_job_and_no_false_commit_event(
        self,
    ) -> None:
        with self.assertRaises(SimulatedProcessCrash):
            self.worker().run_once(crash_after_provider_accepts=True)
        self.assertNotIn(EventKind.SUBMISSION_RECORDED.value, self.observer.counts())
        self.now += 31
        self.assertEqual(self.worker().run_once().outcome, "submitted")
        counts = self.observer.counts()
        self.assertEqual(counts[EventKind.CLAIM.value], 2)
        self.assertEqual(counts[EventKind.PROVIDER_STARTED.value], 2)
        self.assertEqual(counts[EventKind.SUBMISSION_RECORDED.value], 1)
        self.assertEqual(self.provider.jobs_created, 1)
        with self.database.repository() as repository:
            self.assertEqual(len(repository.list_provider_jobs("run-1")), 1)
        responses = [
            event for event in self.events if event.kind is EventKind.PROVIDER_RESPONSE
        ]
        self.assertEqual(responses[0].submission_digest, responses[1].submission_digest)
        self.assertEqual(responses[0].job_ref, responses[1].job_ref)

    def test_callback_outcomes_keep_job_identity_and_commit_before_observation(
        self,
    ) -> None:
        self.database.observer = self.observer
        self.worker().run_once()
        with self.database.repository() as repository:
            first = repository.list_provider_jobs("run-1")[0]
            run = repository.request_retry(
                "run-1",
                expected_plan_version_id=repository.get("run-1").plan_version_id,
            )
            repository.approve(run.id, plan_version_id=run.plan_version_id)
        self.worker().run_once()
        seen = []

        def sink(event: ExecutionEvent) -> None:
            if event.kind is EventKind.COMPLETION_RECORDED:
                with self.database.repository() as independent:
                    seen.append(len(independent.list_provider_events("run-1")))
                self.events.append(event)

        self.observer.sink = sink
        with self.database.repository() as repository:
            second = repository.get("run-1").external_job_id
            assert second is not None
            repository.record_completion(
                external_job_id=first.external_job_id, external_event_id="event-a"
            )
            repository.record_completion(
                external_job_id=second, external_event_id="event-b"
            )
            repository.record_completion(
                external_job_id=second, external_event_id="event-b"
            )
            self.assertEqual(len(repository.generated_videos("run-1")), 1)
            self.assertEqual(
                repository.execution_counts("run-1"),
                {
                    "recorded_provider_jobs": 2,
                    "distinct_jobs_with_completion_reports": 2,
                    "callback_audit_rows": 2,
                    "replacement_video_versions": 1,
                },
            )
        completions = [
            e for e in self.events if e.kind is EventKind.COMPLETION_RECORDED
        ]
        self.assertEqual(
            [e.disposition for e in completions], ["stale", "applied", "redundant"]
        )
        self.assertEqual(completions[0].plan_ref, reference(first.plan_version_id))
        self.assertEqual(completions[1].job_ref, completions[2].job_ref)
        self.assertEqual(seen, [1, 2, 2])

    def test_callback_sink_failure_preserves_result_and_invalid_callback_emits_nothing(
        self,
    ) -> None:
        self.database.observer = self.observer
        self.worker().run_once()

        def broken_sink(event: ExecutionEvent) -> None:
            raise RuntimeError("secret")

        self.observer.sink = broken_sink
        with self.database.repository() as repository:
            result = repository.record_completion(
                external_job_id="video-job-1", external_event_id="event-1"
            )
            self.assertEqual(result.status, "succeeded")
            with self.assertRaises(ValueError):
                repository.record_completion(
                    external_job_id="video-job-1", external_event_id=""
                )
            self.assertEqual(len(repository.generated_videos("run-1")), 1)
        self.assertEqual(self.observer.counts()[EventKind.COMPLETION_RECORDED.value], 1)
        self.assertEqual(self.observer.sink_failures, 2)

    def test_sink_sees_committed_job_on_an_independent_connection(self) -> None:
        committed = []

        def sink(event: ExecutionEvent) -> None:
            if event.kind is EventKind.SUBMISSION_RECORDED:
                with self.database.repository() as repository:
                    run = repository.get("run-1")
                    committed.append((run.status.value, run.external_job_id))

        self.observer.sink = sink
        self.worker().run_once()
        self.assertEqual(committed, [("submitted", "video-job-1")])

    def test_sink_failure_does_not_trigger_retry_or_change_recorded_state(self) -> None:
        def broken_sink(event: ExecutionEvent) -> None:
            raise RuntimeError("secret monitoring failure")

        self.observer.sink = broken_sink
        self.assertEqual(self.worker().run_once().outcome, "submitted")
        self.assertEqual(self.worker().run_once().outcome, "idle")
        self.assertEqual(self.provider.submit_attempts, 1)
        self.assertEqual(self.observer.sink_failures, 6)
        with self.database.repository() as repository:
            self.assertEqual(repository.get("run-1").status, "submitted")

    def test_failed_database_record_is_not_logged_as_committed(self) -> None:
        with (
            patch.object(
                WorkflowRepository,
                "record_submission",
                side_effect=sqlite3.OperationalError("write failed"),
            ),
            self.assertRaises(sqlite3.OperationalError),
        ):
            self.worker().run_once()
        self.assertNotIn(EventKind.SUBMISSION_RECORDED.value, self.observer.counts())
        self.assertEqual(self.provider.jobs_created, 1)
        with self.database.repository() as repository:
            self.assertEqual(repository.get("run-1").status, "submitting")

    def test_provider_duration_excludes_sink_delivery_time(self) -> None:
        def delayed_sink(event: ExecutionEvent) -> None:
            self.elapsed += 5
            self.events.append(event)

        self.observer.sink = delayed_sink
        submit = self.provider.submit

        def timed_submit(*, idempotency_key: str, action: str) -> str:
            self.elapsed += 2
            return submit(idempotency_key=idempotency_key, action=action)

        with patch.object(self.provider, "submit", side_effect=timed_submit):
            self.worker().run_once()
        response = next(
            event for event in self.events if event.kind is EventKind.PROVIDER_RESPONSE
        )
        self.assertEqual(response.duration_seconds, 2)

    def test_timeout_is_classified_without_exception_payload_or_raw_identifiers(
        self,
    ) -> None:
        with patch.object(
            self.provider,
            "submit",
            side_effect=TimeoutError("api-key=secret; private response"),
        ):
            self.assertEqual(self.worker().run_once().outcome, "retry_wait")
        error = next(
            event for event in self.events if event.kind is EventKind.PROVIDER_ERROR
        )
        self.assertEqual(error.error_category, "timeout")
        output = json.dumps([asdict(event) for event in self.events])
        for forbidden in ("secret", "private response", "run-1", "workflow-run:"):
            self.assertNotIn(forbidden, output)
        self.assertEqual(len(error.submission_digest or ""), 64)
        self.assertNotIn(EventKind.SUBMISSION_RECORDED.value, self.observer.counts())

    def test_json_sink_writes_parseable_events_to_stderr(self) -> None:
        self.observer.sink = stderr_event
        output = io.StringIO()
        with redirect_stderr(output):
            self.worker().run_once()
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(
            [record["kind"] for record in records],
            [
                kind.value
                for kind in (
                    EventKind.CLAIM,
                    EventKind.RESERVED,
                    EventKind.PROVIDER_STARTED,
                    EventKind.PROVIDER_RESPONSE,
                    EventKind.SUBMISSION_RECORDED,
                    EventKind.WORKER_RESULT,
                )
            ],
        )
        self.assertEqual(records[0]["attempt"], 1)
        self.assertIsNotNone(records[-2]["job_ref"])

    def test_claim_event_keeps_original_plan_when_another_worker_supersedes_it(
        self,
    ) -> None:
        with self.database.repository() as repository:
            original_run = repository.get("run-1")
        claim = SubmissionQueue.claim

        def supersede_after_claim(
            queue: SubmissionQueue,
            owner: str,
            *,
            run_id: str | None = None,
            expected_plan_version_id: str | None = None,
        ) -> SubmissionLease | None:
            lease = claim(
                queue,
                owner,
                run_id=run_id,
                expected_plan_version_id=expected_plan_version_id,
            )
            if owner == "worker-a" and lease is not None:
                self.now += 31
                result = DurableWorker(
                    self.database,
                    self.provider,
                    owner="worker-b",
                    clock=lambda: self.now,
                ).run_once()
                self.assertEqual(result.outcome, "submitted")
                with self.database.repository() as repository:
                    repository.request_retry(
                        "run-1",
                        expected_plan_version_id=repository.get(
                            "run-1"
                        ).plan_version_id,
                    )
            return lease

        with patch.object(SubmissionQueue, "claim", supersede_after_claim):
            self.assertEqual(self.worker().run_once().outcome, "lease_lost")
        event = next(event for event in self.events if event.kind is EventKind.CLAIM)
        self.assertEqual(event.plan_ref, reference(original_run.plan_version_id))
        self.assertEqual(
            event.submission_digest, reference(original_run.idempotency_key)
        )
        self.assertEqual(event.attempt, 1)
