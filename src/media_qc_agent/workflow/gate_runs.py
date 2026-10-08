"""Run a deterministic quality gate and open a workflow run for its finding.

Each function returns None when its gate finds nothing. Evidence is built from
the gate's own measurements, so every fact cites the exact checked version.
"""

from typing import TYPE_CHECKING

from media_qc_agent.domain.evidence import EvidenceInput, EvidenceRole
from media_qc_agent.domain.models import ArtifactKind, FailureKind, QualityFinding
from media_qc_agent.quality.captions import validate_captions
from media_qc_agent.quality.environment import (
    EnvironmentCheck,
    check_script_avatar_compatibility,
)
from media_qc_agent.quality.visual_quality import MotionSample, check_jerky_video
from media_qc_agent.workflow.artifacts import ArtifactStore
from media_qc_agent.workflow.models import VideoSources, WorkflowRun

if TYPE_CHECKING:
    from media_qc_agent.workflow.repository import WorkflowRepository


def check_environment(
    artifacts: ArtifactStore, sources: VideoSources
) -> EnvironmentCheck:
    return check_script_avatar_compatibility(
        script_version_id=sources.script_version_id,
        authored_text=artifacts.script_text(sources.script_version_id),
        scene=artifacts.script_scene(sources.script_version_id),
        avatar_version_id=sources.avatar_version_id,
        avatar_environment=artifacts.avatar_environment(sources.avatar_version_id),
    )


def open_environment_run(
    repository: "WorkflowRepository", *, run_id: str, sources: VideoSources
) -> WorkflowRun | None:
    """Ask a human to choose the repair when the pre-render gate finds a mismatch."""

    checked = check_environment(repository.artifacts, sources)
    if checked.finding is None or checked.evidence is None:
        return None
    signal = checked.evidence
    evidence = (
        EvidenceInput(
            EvidenceRole.FACT,
            signal.script_version_id,
            f"Script contains scene phrase {signal.script_phrase!r}.",
            observed=signal.script_phrase,
            limit=signal.required_environment.value,
        ),
        EvidenceInput(
            EvidenceRole.FACT,
            signal.avatar_version_id,
            "Avatar declares an environment.",
            observed=signal.avatar_environment.value,
        ),
        EvidenceInput(
            EvidenceRole.INFERENCE,
            signal.script_version_id,
            checked.finding.explanation,
        ),
        EvidenceInput(
            EvidenceRole.UNCERTAINTY,
            signal.avatar_version_id,
            "Declared environment has not been checked against avatar pixels.",
        ),
    )
    return repository.create(
        run_id=run_id, finding=checked.finding, sources=sources, evidence=evidence
    )


def open_visual_quality_run(
    repository: "WorkflowRepository",
    *,
    run_id: str,
    video_version_id: str,
    samples: tuple[MotionSample, ...],
) -> WorkflowRun | None:
    """Propose a video retry only for a repeated same-shot motion jump."""

    sources = repository.artifacts.video_sources(video_version_id)
    checked = check_jerky_video(video_version_id, samples)
    if checked.finding is None:
        return None
    facts = tuple(
        EvidenceInput(
            EvidenceRole.FACT,
            item.video_version_id,
            f"Motion jump from frame {item.from_frame} to {item.to_frame}.",
            observed=f"{item.jump_px_per_frame:g} px/frame",
            limit=f"{item.threshold_px:g} px/frame",
        )
        for item in checked.evidence
    )
    return repository.create(
        run_id=run_id,
        finding=checked.finding,
        sources=sources,
        video_version_id=video_version_id,
        evidence=facts
        + (
            EvidenceInput(
                EvidenceRole.INFERENCE, video_version_id, checked.finding.explanation
            ),
            EvidenceInput(
                EvidenceRole.UNCERTAINTY, video_version_id, checked.demo_notice
            ),
        ),
    )


def open_caption_quality_run(
    repository: "WorkflowRepository", *, run_id: str, caption_version_id: str
) -> WorkflowRun | None:
    """Propose a local caption repair for one exact caption version."""

    artifacts = repository.artifacts
    caption = artifacts.get(caption_version_id)
    if caption.kind is not ArtifactKind.CAPTIONS:
        raise ValueError("caption quality target must be a caption version")
    checked = validate_captions(artifacts.caption_cues(caption_version_id))
    if checked.valid:
        return None
    if len(caption.source_versions) != 1:
        raise ValueError("caption must derive from one video version")
    video_version_id = caption.source_versions[0][1]
    facts = tuple(
        EvidenceInput(
            EvidenceRole.FACT,
            caption_version_id,
            f"Caption rule {item.rule} failed at cue {item.cue_index}.",
            observed=item.observed,
            limit=item.limit,
        )
        for item in checked.evidence
    )
    return repository.create(
        run_id=run_id,
        finding=QualityFinding(
            FailureKind.CAPTION_FORMAT,
            f"{caption_version_id} violates {len(checked.evidence)} caption rule(s).",
            1.0,
        ),
        sources=artifacts.video_sources(video_version_id),
        video_version_id=video_version_id,
        observed_artifact_version_id=caption_version_id,
        evidence=facts
        + (
            EvidenceInput(
                EvidenceRole.INFERENCE,
                caption_version_id,
                "The measured rule violations require local caption repair.",
            ),
            EvidenceInput(
                EvidenceRole.UNCERTAINTY,
                caption_version_id,
                "Caption meaning and visual fit have not been reviewed.",
            ),
        ),
    )
