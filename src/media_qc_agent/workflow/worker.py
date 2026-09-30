"""Execute one leased submission at a time; polling is an assembly concern."""

import time
from collections.abc import Callable

from media_qc_agent.workflow.database import Database
from media_qc_agent.workflow.executor import WorkflowExecutor
from media_qc_agent.workflow.models import WorkflowRun
from media_qc_agent.workflow.provider import VideoProvider
from media_qc_agent.workflow.repository import WorkflowRepository
from media_qc_agent.workflow.worker_models import (
    LeaseLost,
    SubmissionLease,
    WorkerPolicy,
    WorkerResult,
)
from media_qc_agent.workflow.worker_queue import SubmissionQueue, require_lease


class LeasedWorkflowStore:
    def __init__(self, queue: SubmissionQueue, lease: SubmissionLease) -> None:
        self.queue = queue
        self.lease = lease

    def get(self, run_id: str) -> WorkflowRun:
        require_lease(self.queue.connection, self.lease, self.queue.clock())
        return self.queue.repository.get(run_id)

    def reserve_submission(
        self, *, run_id: str, expected_plan_version_id: str | None
    ) -> WorkflowRun:
        return self.queue.repository.reserve_submission(
            run_id=run_id,
            expected_plan_version_id=expected_plan_version_id,
            lease=self.lease,
        )

    def record_submission(
        self, *, run_id: str, external_job_id: str, expected_plan_version_id: str | None
    ) -> WorkflowRun:
        return self.queue.repository.record_submission(
            run_id=run_id,
            external_job_id=external_job_id,
            expected_plan_version_id=expected_plan_version_id,
            lease=self.lease,
        )


class DurableWorker:
    def __init__(
        self,
        database: Database,
        provider: VideoProvider,
        *,
        owner: str,
        policy: WorkerPolicy | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.database = database
        self.provider = provider
        self.owner = owner
        self.policy = policy or WorkerPolicy()
        self.clock = clock

    def run_once(self, *, crash_after_provider_accepts: bool = False) -> WorkerResult:
        with self.database.connection() as connection:
            repository = WorkflowRepository(connection, clock=self.clock)
            queue = SubmissionQueue(connection, repository, self.policy, self.clock)
            lease = queue.claim(self.owner)
            if lease is None:
                return WorkerResult("idle")
            return self._execute(queue, lease, crash_after_provider_accepts)

    def _execute(
        self, queue: SubmissionQueue, lease: SubmissionLease, crash: bool
    ) -> WorkerResult:
        executor = WorkflowExecutor(
            repository=LeasedWorkflowStore(queue, lease), provider=self.provider
        )
        try:
            run = executor.submit(lease.run_id, crash_after_provider_accepts=crash)
            queue.finish(lease)
            return WorkerResult("submitted", run.id, run.external_job_id)
        except LeaseLost:
            return WorkerResult("lease_lost", lease.run_id)
        except (TimeoutError, ConnectionError) as error:
            return self._failure(queue, lease, error, retryable=True)
        except ValueError as error:
            return self._failure(queue, lease, error, retryable=False)

    def _failure(
        self,
        queue: SubmissionQueue,
        lease: SubmissionLease,
        error: Exception,
        *,
        retryable: bool,
    ) -> WorkerResult:
        try:
            outcome = queue.fail(lease, error, retryable=retryable)
        except LeaseLost:
            outcome = "lease_lost"
        return WorkerResult(outcome, lease.run_id)
