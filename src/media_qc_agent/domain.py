"""Domain types shared by model-facing and deterministic workflow code."""

from dataclasses import dataclass
from enum import StrEnum


class ArtifactKind(StrEnum):
    SCRIPT = "script"
    AVATAR = "avatar"
    VOICE = "voice"
    VIDEO = "video"
    CAPTIONS = "captions"


class FailureKind(StrEnum):
    SCRIPT_QUALITY = "script_quality"
    ENVIRONMENT_MISMATCH = "environment_mismatch"
    CAPTION_FORMAT = "caption_format"
    VISUAL_QUALITY = "visual_quality"


class RepairAction(StrEnum):
    REVISE_SCRIPT = "revise_script"
    CHANGE_AVATAR = "change_avatar"
    REPAIR_CAPTIONS = "repair_captions"
    REGENERATE_VIDEO = "regenerate_video"


@dataclass(frozen=True)
class QualityFinding:
    """A structured diagnosis produced by a validator or model evaluator."""

    kind: FailureKind
    explanation: str
    confidence: float

    def __post_init__(self) -> None:
        if not self.explanation.strip():
            raise ValueError("explanation must not be blank")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")


@dataclass(frozen=True)
class RepairPlan:
    """The minimum safe repair proposed for one quality finding."""

    action: RepairAction
    invalidates: frozenset[ArtifactKind]
    requires_repair_input: bool
    rationale: str


@dataclass(frozen=True)
class RepairOption:
    """One concrete branch offered for a finding needing human direction."""

    action: RepairAction
    invalidates: frozenset[ArtifactKind]
    rationale: str


@dataclass(frozen=True)
class ClarificationRequest:
    """A decision required before an executable repair plan can exist."""

    question: str
    options: tuple[RepairOption, ...]
