"""Workflow state shared by persistence and execution."""

from dataclasses import dataclass
from enum import StrEnum

from media_qc_agent.domain.models import (
    ArtifactKind,
    ClarificationRequest,
    RepairAction,
    RepairPlan,
)


class WorkflowStatus(StrEnum):
    NEEDS_INPUT = "needs_input"
    NEEDS_REPAIR_INPUT = "needs_repair_input"
    AWAITING_APPROVAL = "awaiting_approval"
    READY = "ready"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    SUCCEEDED = "succeeded"


class ProviderEventDisposition(StrEnum):
    APPLIED = "applied"
    STALE = "stale"
    REDUNDANT = "redundant"
    REJECTED = "rejected"


@dataclass(frozen=True)
class VideoSources:
    script_version_id: str
    tts_input_version_id: str
    avatar_version_id: str
    voice_version_id: str

    def dependencies(self) -> tuple[tuple[ArtifactKind, str], ...]:
        return (
            (ArtifactKind.SCRIPT, self.script_version_id),
            (ArtifactKind.TTS_INPUT, self.tts_input_version_id),
            (ArtifactKind.AVATAR, self.avatar_version_id),
            (ArtifactKind.VOICE, self.voice_version_id),
        )


@dataclass(frozen=True)
class ArtifactVersion:
    id: str
    kind: ArtifactKind
    source_versions: tuple[tuple[ArtifactKind, str], ...]
    external_job_id: str | None
    created_at: str


@dataclass(frozen=True)
class RepairPlanVersion:
    id: str
    run_id: str
    revision: int
    plan: RepairPlan
    sources: VideoSources
    target_video_version_id: str | None
    target_caption_version_id: str | None
    observed_artifact_version_id: str
    replacement_choices: tuple[tuple[ArtifactKind, str], ...]
    idempotency_key: str | None
    created_at: str


@dataclass(frozen=True)
class PlanApproval:
    plan_version_id: str
    created_at: str


@dataclass(frozen=True)
class WorkflowRun:
    id: str
    status: WorkflowStatus
    plan: RepairPlan | None
    clarification: ClarificationRequest | None
    idempotency_key: str | None
    external_job_id: str | None
    sources: VideoSources
    active_video_version_id: str | None
    created_at: str
    updated_at: str
    active_caption_version_id: str | None = None
    plan_version: RepairPlanVersion | None = None
    approval: PlanApproval | None = None

    @property
    def plan_version_id(self) -> str | None:
        return self.plan_version.id if self.plan_version else None


def has_current_approval(run: WorkflowRun) -> bool:
    """The current plan version is approved and still targets the run's outputs."""

    version = run.plan_version
    return (
        version is not None
        and run.approval is not None
        and run.approval.plan_version_id == version.id
        and version.run_id == run.id
        and version.target_video_version_id == run.active_video_version_id
        and version.target_caption_version_id == run.active_caption_version_id
    )


def require_video_submission(run: WorkflowRun) -> None:
    """The one pre-submission check, applied by the store and the executor."""

    if run.status not in {WorkflowStatus.READY, WorkflowStatus.SUBMITTING}:
        raise ValueError("workflow must be approved before provider submission")
    require_approved_video_plan(run)


def require_approved_video_plan(run: WorkflowRun) -> None:
    if run.plan is None or run.idempotency_key is None:
        raise ValueError("workflow has no executable repair plan")
    if run.plan.action is RepairAction.REPAIR_CAPTIONS:
        raise ValueError("caption repair cannot submit a video provider job")
    if not has_current_approval(run):
        raise ValueError("workflow has no current plan-version approval")


@dataclass(frozen=True)
class ProviderJob:
    """A submitted job; its action and sources are those of its plan version."""

    external_job_id: str
    run_id: str
    plan_version_id: str
    idempotency_key: str
    action: str
    sources: VideoSources
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
