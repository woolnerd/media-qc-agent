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
