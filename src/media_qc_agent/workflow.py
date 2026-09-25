"""Workflow state shared by persistence and execution."""

from dataclasses import dataclass
from enum import StrEnum

from .domain import ClarificationRequest, RepairPlan


class WorkflowStatus(StrEnum):
    NEEDS_INPUT = "needs_input"
    NEEDS_REPAIR_INPUT = "needs_repair_input"
    AWAITING_APPROVAL = "awaiting_approval"
    READY = "ready"
    SUBMITTED = "submitted"
    SUCCEEDED = "succeeded"


class ProviderEventDisposition(StrEnum):
    APPLIED = "applied"
    STALE = "stale"
    REDUNDANT = "redundant"
    REJECTED = "rejected"


@dataclass(frozen=True)
class WorkflowRun:
    id: str
    status: WorkflowStatus
    plan: RepairPlan | None
    clarification: ClarificationRequest | None
    idempotency_key: str | None
    external_job_id: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProviderJob:
    external_job_id: str
    run_id: str
    idempotency_key: str
    action: str
    created_at: str


@dataclass(frozen=True)
class ProviderEvent:
    external_event_id: str
    external_job_id: str
    event_type: str
    result_status: WorkflowStatus
    disposition: ProviderEventDisposition
    reason: str | None
    created_at: str


def classify_completion(
    *, job_is_active: bool, status: WorkflowStatus
) -> tuple[ProviderEventDisposition, str | None]:
    """Decide whether a provider completion may advance the current run."""

    if not job_is_active:
        return ProviderEventDisposition.STALE, "provider job is no longer active"
    if status is WorkflowStatus.SUBMITTED:
        return ProviderEventDisposition.APPLIED, None
    if status is WorkflowStatus.SUCCEEDED:
        return ProviderEventDisposition.REDUNDANT, "provider job already completed"
    return (
        ProviderEventDisposition.REJECTED,
        f"active provider job cannot complete while workflow is {status.value}",
    )
