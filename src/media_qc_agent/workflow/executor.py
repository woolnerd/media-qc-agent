"""Submit approved workflow work through a provider boundary."""

from typing import Protocol

from media_qc_agent.domain.models import RepairAction
from media_qc_agent.workflow.models import (
    WorkflowRun,
    WorkflowStatus,
    has_current_approval,
)
from media_qc_agent.workflow.provider import VideoProvider
from media_qc_agent.workflow.telemetry import (
    EventKind,
    ExecutionObserver,
    error_category,
    event_for_run,
)
from media_qc_agent.workflow.tracing import provider_span


class WorkflowStore(Protocol):
    """Persistence operations needed by provider execution."""

    def get(self, run_id: str) -> WorkflowRun: ...

    def reserve_submission(
        self, *, run_id: str, expected_plan_version_id: str | None
    ) -> WorkflowRun: ...

    def record_submission(
        self,
        *,
        run_id: str,
        external_job_id: str,
        expected_plan_version_id: str | None,
        traceparent: str | None = None,
    ) -> WorkflowRun: ...


class SimulatedProcessCrash(RuntimeError):
    """Raised by a test hook at the external-side-effect crash gap."""


class WorkflowExecutor:
    def __init__(
        self,
        *,
        repository: WorkflowStore,
        provider: VideoProvider,
        observer: ExecutionObserver | None = None,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._observer = observer or ExecutionObserver()
        self._traceparent: str | None = None

    def submit(
        self,
        run_id: str,
        *,
        crash_after_provider_accepts: bool = False,
    ) -> WorkflowRun:
        run = self._repository.get(run_id)
        if run.status is WorkflowStatus.SUBMITTED:
            return run
        self._validate_submission(run)
        run = self._repository.reserve_submission(
            run_id=run.id, expected_plan_version_id=run.plan_version_id
        )
        if run.status is WorkflowStatus.SUBMITTED:
            return run
        self._validate_submission(run)
        assert run.plan is not None
        assert run.idempotency_key is not None

        external_job_id = self._submit_provider(run)

        if crash_after_provider_accepts:
            raise SimulatedProcessCrash(
                "provider accepted the job before local state was recorded"
            )

        recorded = self._repository.record_submission(
            run_id=run.id,
            external_job_id=external_job_id,
            expected_plan_version_id=run.plan_version_id,
            traceparent=self._traceparent,
        )
        self._observer.emit(
            event_for_run(
                EventKind.SUBMISSION_RECORDED,
                run,
                job_id=external_job_id,
                state_before=WorkflowStatus.SUBMITTING,
                state_after=WorkflowStatus.SUBMITTED,
            )
        )
        return recorded

    def _submit_provider(self, run: WorkflowRun) -> str:
        assert run.plan is not None and run.idempotency_key is not None
        self._observer.emit(event_for_run(EventKind.PROVIDER_STARTED, run))
        event = None
        try:
            with provider_span(
                self._observer.tracer, run, self._observer.trace_failed
            ) as span:
                started = self._observer.clock()
                try:
                    job_id = self._provider.submit(
                        idempotency_key=run.idempotency_key, action=run.plan.action
                    )
                except Exception as error:
                    category = error_category(error)
                    event = event_for_run(
                        EventKind.PROVIDER_ERROR,
                        run,
                        duration_seconds=self._observer.clock() - started,
                        error=category,
                    )
                    span.error(category)
                    raise
                event = event_for_run(
                    EventKind.PROVIDER_RESPONSE,
                    run,
                    job_id=job_id,
                    duration_seconds=self._observer.clock() - started,
                )
                span.response(job_id)
                self._traceparent = span.traceparent()
            return job_id
        finally:
            if event is not None:
                self._observer.emit(event)

    def _validate_submission(self, run: WorkflowRun) -> None:
        if run.status not in {WorkflowStatus.READY, WorkflowStatus.SUBMITTING}:
            raise ValueError("workflow must be approved before submission")
        if run.plan is None or run.idempotency_key is None:
            raise ValueError("workflow has no executable repair plan")
        if not has_current_approval(run):
            raise ValueError("workflow has no current plan-version approval")
        if run.plan.action is RepairAction.REPAIR_CAPTIONS:
            raise ValueError("caption repair must not submit a video provider job")
