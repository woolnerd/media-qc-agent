"""Exercise durable claims with independent connections and controlled time."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import patch

from media_qc_agent.api.scenarios import create_scenario_run, seed_scenarios
from media_qc_agent.workflow.database import Database
from media_qc_agent.workflow.durable_provider import DurableFakeVideoProvider
from media_qc_agent.workflow.executor import SimulatedProcessCrash
from media_qc_agent.workflow.repository import WorkflowRepository
from media_qc_agent.workflow.worker import DurableWorker
from media_qc_agent.workflow.worker_models import LeaseLost, WorkerPolicy
from media_qc_agent.workflow.worker_queue import SubmissionQueue


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class WorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "workflow.sqlite3"
        self.database = Database(self.path)
        self.provider_path = self.path.with_suffix(".provider.sqlite3")
        self.provider = DurableFakeVideoProvider(self.provider_path)
        self.clock = Clock()
        with self.database.repository() as repository:
            repository.initialize()
            seed_scenarios(repository)

    def create(
        self,
        run_id: str = "run-1",
        scenario: str = "jerky-video",
        *,
        approve: bool = True,
    ) -> None:
        with self.database.repository() as repository:
            run = create_scenario_run(repository, scenario, run_id)
            if approve:
                repository.approve(run_id, plan_version_id=run.plan_version_id)

    def worker(
        self, *, owner: str = "worker-a", policy: WorkerPolicy | None = None
    ) -> DurableWorker:
        return DurableWorker(
            self.database, self.provider, owner=owner, policy=policy, clock=self.clock
        )

    @contextmanager
    def queue(self, policy: WorkerPolicy | None = None) -> Iterator[SubmissionQueue]:
        with self.database.connection() as connection:
            repository = WorkflowRepository(connection, clock=self.clock)
            yield SubmissionQueue(
                connection, repository, policy or WorkerPolicy(), self.clock
            )

    def test_only_approved_video_plans_are_claimed(self) -> None:
        self.create("unapproved", approve=False)
        self.create("caption-run", "caption-format")
        self.create("approved")
        result = self.worker().run_once()
        self.assertEqual(result.run_id, "approved")
        self.assertEqual(result.outcome, "submitted")
        self.assertEqual(self.worker().run_once().outcome, "idle")
        with self.database.repository() as repository:
            self.assertEqual(repository.get("unapproved").status, "awaiting_approval")
            self.assertEqual(repository.get("caption-run").status, "ready")

    def test_claim_reserves_plan_before_provider_call_and_blocks_edits(self) -> None:
        self.create()
        submit = self.provider.submit

        def check_reserved(*, idempotency_key: str, action: str) -> str:
            with self.database.repository() as repository:
                current = repository.get("run-1")
                self.assertEqual(current.status, "submitting")
                with self.assertRaises(ValueError):
                    repository.bind_replacement("run-1", "avatar-api-kitchen")
            return submit(idempotency_key=idempotency_key, action=action)

        with patch.object(self.provider, "submit", side_effect=check_reserved):
            self.assertEqual(self.worker().run_once().outcome, "submitted")

    def test_expired_lease_recovers_after_acceptance_without_second_job(self) -> None:
        self.create()
        with self.assertRaises(SimulatedProcessCrash):
            self.worker().run_once(crash_after_provider_accepts=True)
        self.assertEqual(self.provider.jobs_created, 1)
        self.assertEqual(self.worker(owner="worker-b").run_once().outcome, "idle")
        self.clock.now += 30
        restarted = DurableFakeVideoProvider(self.provider_path)
        result = DurableWorker(
            self.database, restarted, owner="worker-b", clock=self.clock
        ).run_once()
        self.assertEqual(result.outcome, "submitted")
        self.assertEqual(restarted.jobs_created, 1)
        with self.database.repository() as repository:
            self.assertEqual(
                repository.get("run-1").external_job_id, result.external_job_id
            )

    def test_subprocess_restart_uses_provider_owned_persisted_idempotency(self) -> None:
        self.create()
        with self.assertRaises(SimulatedProcessCrash):
            self.worker().run_once(crash_after_provider_accepts=True)
        # The first worker used a past wall clock; the restarted process sees its lease expired.
        source = Path(__file__).resolve().parents[2] / "src"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "media_qc_agent.cli.worker",
                "--once",
                "--database",
                str(self.path),
            ],
            env={**os.environ, "PYTHONPATH": str(source)},
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        self.assertEqual(json.loads(result.stdout)["outcome"], "submitted")
        self.assertEqual(DurableFakeVideoProvider(self.provider_path).jobs_created, 1)

    def test_two_workers_cannot_claim_the_same_live_lease(self) -> None:
        self.create()
        barrier = Barrier(2)

        def claim(owner: str) -> object:
            with self.queue() as queue:
                barrier.wait(timeout=5)
                return queue.claim(owner)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim, owner) for owner in ("worker-a", "worker-b")]
            claims = [future.result(timeout=15) for future in futures]
        self.assertEqual(sum(lease is not None for lease in claims), 1)
        self.assertEqual(self.provider.jobs_created, 0)

    def test_capacity_counts_submitted_jobs_until_callback_completes(self) -> None:
        self.create("run-1")
        self.create("run-2")
        worker = self.worker(policy=WorkerPolicy(max_in_flight=1))
        first = worker.run_once()
        self.assertEqual(worker.run_once().outcome, "idle")
        assert first.external_job_id is not None
        with self.database.repository() as repository:
            repository.record_completion(
                external_job_id=first.external_job_id, external_event_id="done-1"
            )
        self.assertEqual(worker.run_once().run_id, "run-2")

    def test_unknown_acceptance_holds_capacity_but_same_attempt_can_recover(
        self,
    ) -> None:
        self.create("run-1")
        self.create("run-2")
        worker = self.worker(policy=WorkerPolicy(max_in_flight=1))
        with self.assertRaises(SimulatedProcessCrash):
            worker.run_once(crash_after_provider_accepts=True)
        self.assertEqual(worker.run_once().outcome, "idle")
        self.clock.now += 30
        result = worker.run_once()
        self.assertEqual(result.run_id, "run-1")
        self.assertEqual(result.outcome, "submitted")
        self.assertEqual(worker.run_once().outcome, "idle")
        self.assertEqual(self.provider.jobs_created, 1)

    def test_timeouts_back_off_and_stop_at_durable_attempt_limit(self) -> None:
        self.create()
        worker = self.worker()
        with patch.object(
            self.provider, "submit", side_effect=TimeoutError("secret response")
        ) as submit:
            self.assertEqual(worker.run_once().outcome, "retry_wait")
            self.assertEqual(worker.run_once().outcome, "idle")
            self.clock.now += 2
            self.assertEqual(worker.run_once().outcome, "retry_wait")
            self.clock.now += 3
            self.assertEqual(worker.run_once().outcome, "idle")
            self.clock.now += 1
            self.assertEqual(worker.run_once().outcome, "stopped")
            self.clock.now += 10000
            self.assertEqual(self.worker(owner="restarted").run_once().outcome, "idle")
            self.assertEqual(submit.call_count, 3)
        with self.queue() as queue:
            row = queue.connection.execute("SELECT * FROM worker_attempts").fetchone()
            self.assertEqual(row["attempts"], 3)
            self.assertEqual(row["error_type"], "TimeoutError")
            self.assertNotIn("secret", repr(tuple(row)))
            self.assertEqual(queue.repository.get("run-1").status, "submitting")

    def test_crashed_claims_also_consume_the_attempt_budget(self) -> None:
        self.create()
        with self.queue(WorkerPolicy(max_attempts=2)) as queue:
            self.assertIsNotNone(queue.claim("worker-a"))
            self.clock.now += 30
            self.assertIsNotNone(queue.claim("worker-b"))
            self.clock.now += 30
            self.assertIsNone(queue.claim("worker-c"))

    def test_lost_acceptance_response_retries_same_key_after_backoff(self) -> None:
        self.create()
        submit = self.provider.submit

        def accept_then_timeout(*, idempotency_key: str, action: str) -> str:
            submit(idempotency_key=idempotency_key, action=action)
            raise TimeoutError("acceptance response lost")

        with patch.object(self.provider, "submit", side_effect=accept_then_timeout):
            self.assertEqual(self.worker().run_once().outcome, "retry_wait")
        self.clock.now += 2
        self.assertEqual(self.worker(owner="restarted").run_once().outcome, "submitted")
        self.assertEqual(self.provider.jobs_created, 1)

    def test_exhausted_unknown_outcome_keeps_capacity_reserved(self) -> None:
        self.create("run-1")
        self.create("run-2")
        policy = WorkerPolicy(max_in_flight=1, max_attempts=1)
        with patch.object(self.provider, "submit", side_effect=TimeoutError()):
            self.assertEqual(self.worker(policy=policy).run_once().outcome, "stopped")
        self.clock.now += 1000
        self.assertEqual(self.worker(policy=policy).run_once().outcome, "idle")
        with self.database.repository() as repository:
            self.assertEqual(repository.get("run-2").status, "ready")

    def test_nonretryable_provider_error_stops_without_releasing_reservation(
        self,
    ) -> None:
        self.create()
        with patch.object(
            self.provider, "submit", side_effect=ValueError("bad action")
        ):
            self.assertEqual(self.worker().run_once().outcome, "stopped")
        self.clock.now += 1000
        self.assertEqual(self.worker().run_once().outcome, "idle")
        with self.database.repository() as repository:
            self.assertEqual(repository.get("run-1").status, "submitting")

    def test_expired_owner_cannot_reserve_record_or_release_new_lease(self) -> None:
        self.create()
        with self.queue() as queue:
            old = queue.claim("same-owner")
            assert old is not None
            self.clock.now += 30
            current = queue.claim("same-owner")
            assert current is not None
            with self.assertRaises(LeaseLost):
                queue.repository.reserve_submission(
                    run_id="run-1",
                    expected_plan_version_id=old.plan_version_id,
                    lease=old,
                )
            with self.assertRaises(LeaseLost):
                queue.repository.record_submission(
                    run_id="run-1",
                    external_job_id="old-job",
                    expected_plan_version_id=old.plan_version_id,
                    lease=old,
                )
            with self.assertRaises(LeaseLost):
                queue.finish(old)
            with self.assertRaises(LeaseLost):
                queue.fail(old, TimeoutError(), retryable=True)
            queue.repository.reserve_submission(
                run_id="run-1",
                expected_plan_version_id=current.plan_version_id,
                lease=current,
            )

    def test_policy_is_shared_and_cannot_silently_raise_limits_on_restart(self) -> None:
        with self.queue(WorkerPolicy(max_in_flight=1)):
            pass
        with (
            self.assertRaisesRegex(ValueError, "persisted policy"),
            self.queue(WorkerPolicy(max_in_flight=2)),
        ):
            pass

    def test_slow_expired_owner_cannot_acknowledge_new_workers_submission(self) -> None:
        self.create()
        accepted = Event()
        release = Event()
        submit = self.provider.submit

        def slow_submit(*, idempotency_key: str, action: str) -> str:
            job_id = submit(idempotency_key=idempotency_key, action=action)
            accepted.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test did not release accepted provider response")
            return job_id

        with (
            ThreadPoolExecutor(max_workers=1) as pool,
            patch.object(self.provider, "submit", side_effect=slow_submit),
        ):
            first = pool.submit(self.worker().run_once)
            self.assertTrue(accepted.wait(timeout=5))
            self.clock.now += 30
            restarted = DurableWorker(
                self.database,
                DurableFakeVideoProvider(self.provider_path),
                owner="worker-b",
                clock=self.clock,
            )
            try:
                second = restarted.run_once()
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10).outcome, "lease_lost")
        self.assertEqual(second.outcome, "submitted")
        self.assertEqual(self.provider.jobs_created, 1)

    def test_explicit_retry_gets_new_approval_key_and_attempt_budget(self) -> None:
        self.create()
        self.worker().run_once()
        with self.database.repository() as repository:
            retry = repository.request_retry("run-1")
        self.assertEqual(self.worker().run_once().outcome, "idle")
        with self.database.repository() as repository:
            repository.approve("run-1", plan_version_id=retry.plan_version_id)
        self.assertEqual(self.worker().run_once().outcome, "submitted")
        self.assertEqual(self.provider.jobs_created, 2)

    def test_lease_cannot_authorize_a_different_run(self) -> None:
        self.create("run-1")
        self.create("run-2")
        with self.queue() as queue:
            lease = queue.claim("worker-a")
            assert lease is not None
            other = queue.repository.get("run-2")
            with self.assertRaises(LeaseLost):
                queue.repository.reserve_submission(
                    run_id=other.id,
                    expected_plan_version_id=other.plan_version_id,
                    lease=lease,
                )

    def test_explicit_retry_does_not_hide_old_outstanding_job_from_capacity(
        self,
    ) -> None:
        self.create()
        worker = self.worker(policy=WorkerPolicy(max_in_flight=1))
        old = worker.run_once()
        assert old.external_job_id is not None
        with self.database.repository() as repository:
            retry = repository.request_retry("run-1")
            repository.approve("run-1", plan_version_id=retry.plan_version_id)
        self.assertEqual(worker.run_once().outcome, "idle")
        with self.database.repository() as repository:
            repository.record_completion(
                external_job_id=old.external_job_id, external_event_id="old-finished"
            )
        self.assertEqual(worker.run_once().outcome, "submitted")


class WorkerPolicyTests(unittest.TestCase):
    def test_invalid_policy_is_rejected(self) -> None:
        for policy in (
            {"max_attempts": 0},
            {"max_in_flight": True},
            {"lease_seconds": float("nan")},
            {"lease_seconds": float("inf")},
            {"backoff_seconds": 0},
            {"max_backoff_seconds": 1},
        ):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                WorkerPolicy(**policy)  # type: ignore[arg-type]

    def test_backoff_is_capped(self) -> None:
        policy = WorkerPolicy()
        self.assertEqual(policy.retry_delay(1), 2)
        self.assertEqual(policy.retry_delay(10), 30)
