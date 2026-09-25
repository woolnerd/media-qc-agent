"""Submit approved workflow work through a provider boundary."""

from typing import Protocol

from .domain import RepairAction
from .provider import VideoProvider
from .workflow import WorkflowRun, WorkflowStatus


class WorkflowStore(Protocol):
    """Persistence operations needed by provider execution."""

    def get(self, run_id: str) -> WorkflowRun: ...

    def record_submission(
        self, *, run_id: str, external_job_id: str
    ) -> WorkflowRun: ...


class SimulatedProcessCrash(RuntimeError):
    """Raised by a test hook at the external-side-effect crash gap."""


class WorkflowExecutor:
    def __init__(
        self,
        *,
        repository: WorkflowStore,
        provider: VideoProvider,
    ) -> None:
        self._repository = repository
        self._provider = provider

    def submit(
        self,
        run_id: str,
        *,
        crash_after_provider_accepts: bool = False,
    ) -> WorkflowRun:
        run = self._repository.get(run_id)
        if run.status is WorkflowStatus.SUBMITTED:
            return run
        if run.status is not WorkflowStatus.READY:
            raise ValueError("workflow must be approved before submission")
        if run.plan is None or run.idempotency_key is None:
            raise ValueError("workflow has no executable repair plan")
        if run.plan.action is RepairAction.REPAIR_CAPTIONS:
            raise ValueError("caption repair must not submit a video provider job")

        external_job_id = self._provider.submit(
            idempotency_key=run.idempotency_key,
            action=run.plan.action,
        )

        if crash_after_provider_accepts:
            raise SimulatedProcessCrash(
                "provider accepted the job before local state was recorded"
            )

        return self._repository.record_submission(
            run_id=run.id,
            external_job_id=external_job_id,
        )
