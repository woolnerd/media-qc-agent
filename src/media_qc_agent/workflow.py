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
    created_at: str
