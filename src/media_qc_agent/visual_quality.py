"""Synthetic frame-motion evidence for a bounded jerky-video signal."""

import math
from dataclasses import dataclass
from itertools import pairwise

from .domain import FailureKind, QualityFinding

MOTION_JUMP_THRESHOLD_PX = 12.0
MIN_ABRUPT_CHANGES = 2
MIN_SAMPLES = 4


@dataclass(frozen=True)
class MotionSample:
    frame_index: int
    displacement_px: float
    shot_boundary: bool = False


@dataclass(frozen=True)
class VisualEvidence:
    video_version_id: str
    from_frame: int
    to_frame: int
    jump_px_per_frame: float
    threshold_px: float


@dataclass(frozen=True)
class VisualSignalCheck:
    finding: QualityFinding | None
    evidence: tuple[VisualEvidence, ...]
    review_needed: bool
    reason: str | None


def _validate_samples(samples: tuple[MotionSample, ...]) -> None:
    previous_index: int | None = None
    for sample in samples:
        if sample.frame_index < 0:
            raise ValueError("frame indices must be nonnegative")
        if not math.isfinite(sample.displacement_px) or sample.displacement_px < 0:
            raise ValueError("motion displacement must be finite and nonnegative")
        if previous_index is not None and sample.frame_index != previous_index + 1:
            raise ValueError("motion samples must use contiguous frame indices")
        previous_index = sample.frame_index


def _abrupt_changes(
    video_version_id: str,
    samples: tuple[MotionSample, ...],
    threshold_px: float,
) -> tuple[VisualEvidence, ...]:
    evidence: list[VisualEvidence] = []
    for previous, current in pairwise(samples):
        if previous.shot_boundary or current.shot_boundary:
            continue
        jump = abs(current.displacement_px - previous.displacement_px)
        if jump > threshold_px:
            evidence.append(
                VisualEvidence(
                    video_version_id=video_version_id,
                    from_frame=previous.frame_index,
                    to_frame=current.frame_index,
                    jump_px_per_frame=jump,
                    threshold_px=threshold_px,
                )
            )
    return tuple(evidence)


def check_jerky_video(
    video_version_id: str,
    samples: tuple[MotionSample, ...],
    *,
    threshold_px: float = MOTION_JUMP_THRESHOLD_PX,
) -> VisualSignalCheck:
    """Flag repeated abrupt changes in synthetic same-shot motion measurements."""

    if not video_version_id.strip():
        raise ValueError("video version ID must not be blank")
    if not math.isfinite(threshold_px) or threshold_px <= 0:
        raise ValueError("motion threshold must be finite and positive")
    _validate_samples(samples)
    if len(samples) < MIN_SAMPLES:
        return VisualSignalCheck(None, (), True, "too few motion samples")
    evidence = _abrupt_changes(video_version_id, samples, threshold_px)
    if len(evidence) < MIN_ABRUPT_CHANGES:
        return VisualSignalCheck(
            None,
            evidence,
            bool(evidence),
            "one abrupt change needs reviewer context" if evidence else None,
        )
    finding = QualityFinding(
        kind=FailureKind.VISUAL_QUALITY,
        explanation=(
            f"{video_version_id} has {len(evidence)} same-shot motion jumps "
            f"above {threshold_px:g} px per frame."
        ),
        confidence=0.85,
    )
    return VisualSignalCheck(finding, evidence, False, None)
