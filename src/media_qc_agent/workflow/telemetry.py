"""Best-effort, allowlisted execution observations; never workflow authority."""

import json
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from threading import Lock

from opentelemetry import trace
from opentelemetry.trace import Tracer

from media_qc_agent.domain.ids import reference
from media_qc_agent.workflow.models import (
    ProviderEventDisposition,
    ProviderJob,
    RepairPlanVersion,
    WorkflowRun,
    WorkflowStatus,
)


class EventKind(StrEnum):
    CLAIM = "worker.claimed"
    PROVIDER_STARTED = "provider.submit.started"
    PROVIDER_RESPONSE = "provider.submit.response"
    PROVIDER_ERROR = "provider.submit.error"
    SUBMISSION_RECORDED = "workflow.submission.recorded"
    COMPLETION_RECORDED = "workflow.completion.recorded"
    RESERVED = "workflow.submission.reserved"
    APPROVED = "workflow.plan.approved"
    NEW_ATTEMPT = "workflow.attempt.requested"
    WORKER_RESULT = "worker.finished"
    ARTIFACT_CREATED = "workflow.artifact.created"


class WorkerOutcome(StrEnum):
    SUBMITTED = "submitted"
    RETRY_WAIT = "retry_wait"
    STOPPED = "stopped"
    LEASE_LOST = "lease_lost"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class ErrorCategory(StrEnum):
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    REJECTED = "rejected"
    UNEXPECTED = "unexpected"


def error_category(error: Exception) -> ErrorCategory:
    if isinstance(error, TimeoutError):
        return ErrorCategory.TIMEOUT
    if isinstance(error, ConnectionError):
        return ErrorCategory.CONNECTION
    if isinstance(error, ValueError):
        return ErrorCategory.REJECTED
    return ErrorCategory.UNEXPECTED


@dataclass(frozen=True)
class ExecutionEvent:
    kind: EventKind
    run_ref: str | None
    plan_ref: str | None
    submission_digest: str | None
    job_ref: str | None = None
    attempt: int | None = None
    duration_seconds: float | None = None
    error_category: ErrorCategory | None = None
    disposition: ProviderEventDisposition | None = None
    event_ref: str | None = None
    outcome: WorkerOutcome | None = None
    retry_delay_seconds: float | None = None
    artifact_ref: str | None = None
    artifact_kind: str | None = None
    state_before: WorkflowStatus | None = None
    state_after: WorkflowStatus | None = None


def event_for_completion(
    job: ProviderJob, event_id: str, disposition: ProviderEventDisposition
) -> ExecutionEvent:
    return ExecutionEvent(
        EventKind.COMPLETION_RECORDED,
        reference(job.run_id),
        reference(job.plan_version_id),
        reference(job.idempotency_key),
        reference(job.external_job_id),
        disposition=disposition,
        event_ref=reference(event_id),
    )


def event_for_run(
    kind: EventKind,
    run: WorkflowRun,
    *,
    job_id: str | None = None,
    attempt: int | None = None,
    duration_seconds: float | None = None,
    error: ErrorCategory | None = None,
    state_before: WorkflowStatus | None = None,
    state_after: WorkflowStatus | None = None,
) -> ExecutionEvent:
    return ExecutionEvent(
        kind,
        reference(run.id),
        reference(run.plan_version_id),
        reference(run.idempotency_key),
        reference(job_id),
        attempt,
        duration_seconds,
        error,
        state_before=state_before,
        state_after=state_after,
    )


def event_for_claim(plan: RepairPlanVersion, attempt: int) -> ExecutionEvent:
    return ExecutionEvent(
        EventKind.CLAIM,
        reference(plan.run_id),
        reference(plan.id),
        reference(plan.idempotency_key),
        attempt=attempt,
    )


class ExecutionObserver:
    """Process-local observed-event counts, not durable unique-job accounting.

    Sink callbacks must be fast/bounded. No exception text or input content is
    emitted. Digests correlate events; they do not anonymize guessable values.
    Missing observations remain possible across crashes and sink failures.
    """

    def __init__(
        self,
        sink: Callable[[ExecutionEvent], None] | None = None,
        *,
        clock: Callable[[], float] = time.perf_counter,
        tracer: Tracer | None = None,
        metric_sink: Callable[[ExecutionEvent], None] | None = None,
    ) -> None:
        self.sink = sink
        self.metric_sink = metric_sink
        self._metric_failures = 0
        self.clock = clock
        self.tracer = tracer or trace.get_tracer("media_qc_agent.workflow")
        self._trace_failures = 0
        self._counts: Counter[EventKind] = Counter()
        self._sink_failures = 0
        self._lock = Lock()

    def emit(self, event: ExecutionEvent) -> None:
        with self._lock:
            self._counts[event.kind] += 1
        if self.metric_sink is not None:
            try:
                self.metric_sink(event)
            except Exception:  # noqa: BLE001 — metrics must not retry workflow work
                with self._lock:
                    self._metric_failures += 1
        if self.sink is None:
            return
        try:
            self.sink(event)
        except Exception:  # noqa: BLE001 — isolate any sink failure from business work
            # Telemetry failure must not become a provider retry or undo a commit.
            with self._lock:
                self._sink_failures += 1

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {kind.value: count for kind, count in self._counts.items()}

    def trace_failed(self) -> None:
        with self._lock:
            self._trace_failures += 1

    @property
    def trace_failures(self) -> int:
        with self._lock:
            return self._trace_failures

    @property
    def metric_failures(self) -> int:
        with self._lock:
            return self._metric_failures

    @property
    def sink_failures(self) -> int:
        with self._lock:
            return self._sink_failures


def stderr_event(event: ExecutionEvent) -> None:
    print(json.dumps(asdict(event), sort_keys=True), file=sys.stderr, flush=True)
