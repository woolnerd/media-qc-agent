"""Read a consistent review snapshot without putting SQL in HTML rendering."""

import math
import sqlite3
from dataclasses import dataclass

from media_qc_agent.domain.evidence import EvidenceRecord, QualityFindingRecord
from media_qc_agent.domain.models import ArtifactKind
from media_qc_agent.workflow.models import (
    ArtifactVersion,
    PlanApproval,
    ProviderEvent,
    ProviderJob,
    RepairPlanVersion,
    WorkflowRun,
)
from media_qc_agent.workflow.repository import WorkflowRepository
from media_qc_agent.workflow.worker_models import WorkerPolicy
from media_qc_agent.workflow.worker_queue import (
    load_worker_policy,
    outstanding_capacity,
)


@dataclass(frozen=True)
class RecoveryInfo:
    attempts: int
    lease_remaining: int
    retry_remaining: int
    stopped: bool
    capacity_used: int
    policy: WorkerPolicy


@dataclass(frozen=True)
class ReviewSnapshot:
    run: WorkflowRun
    finding: QualityFindingRecord
    evidence: tuple[EvidenceRecord, ...]
    versions: tuple[RepairPlanVersion, ...]
    approvals: tuple[PlanApproval | None, ...]
    artifacts: tuple[ArtifactVersion, ...]
    descriptions: tuple[str, ...]
    jobs: tuple[ProviderJob, ...]
    events: tuple[ProviderEvent, ...]
    videos: tuple[ArtifactVersion, ...]
    recovery: RecoveryInfo
    accepted_job: str | None

    @property
    def replacement_count(self) -> int:
        current_jobs = {
            job.external_job_id
            for job in self.jobs
            if job.plan_version_id == self.run.plan_version_id
        }
        return sum(video.external_job_id in current_jobs for video in self.videos)


def recovery_info(
    connection: sqlite3.Connection, run: WorkflowRun, now: float
) -> RecoveryInfo:
    policy = load_worker_policy(connection)
    row = connection.execute(
        "SELECT * FROM worker_attempts WHERE plan_version_id = ?",
        (run.plan_version_id,),
    ).fetchone()
    if row is None:
        return RecoveryInfo(0, 0, 0, False, outstanding_capacity(connection), policy)
    lease_left = max(0, math.ceil((row["lease_until"] or 0) - now))
    retry_left = max(0, math.ceil(row["next_attempt_at"] - now))
    stopped = run.status.value == "submitting" and (
        bool(row["stopped"])
        or (row["attempts"] >= policy.max_attempts and lease_left == 0)
    )
    return RecoveryInfo(
        row["attempts"],
        lease_left,
        retry_left,
        stopped,
        outstanding_capacity(connection),
        policy,
    )


def artifact_description(
    repository: WorkflowRepository, artifact: ArtifactVersion
) -> str:
    if artifact.kind is ArtifactKind.SCRIPT:
        return repository.get_script_text(artifact.id)
    if artifact.kind is ArtifactKind.TTS_INPUT:
        return repository.get_tts_input_version(artifact.id).spoken_text
    if artifact.kind is ArtifactKind.AVATAR:
        return (
            "Declared environment: "
            + repository.get_avatar_environment(artifact.id).value
        )
    return "Immutable " + artifact.kind.value.replace("_", " ") + " version"


def review_snapshot(
    connection: sqlite3.Connection,
    repository: WorkflowRepository,
    run_id: str,
    now: float,
    accepted_job: str | None,
) -> ReviewSnapshot:
    run = repository.get(run_id)
    finding = repository.get_quality_finding(run_id)
    ids = list(
        dict.fromkeys(
            [
                *(version for _, version in run.sources.dependencies()),
                finding.artifact_version_id,
                run.active_video_version_id,
                run.active_caption_version_id,
            ]
        )
    )
    artifacts = tuple(
        repository.get_artifact_version(version)
        for version in ids
        if version is not None
    )
    versions = repository.get_plan_versions(run_id)
    return ReviewSnapshot(
        run,
        finding,
        repository.get_quality_evidence(finding.id),
        versions,
        tuple(repository.get_plan_approval(version.id) for version in versions),
        artifacts,
        tuple(artifact_description(repository, artifact) for artifact in artifacts),
        repository.list_provider_jobs(run_id),
        repository.list_provider_events(run_id),
        repository.generated_videos(run_id),
        recovery_info(connection, run, now),
        accepted_job,
    )
