"""Synthetic script and avatar environment compatibility checks."""

import re
from dataclasses import dataclass
from enum import StrEnum

from media_qc_agent.domain.models import FailureKind, QualityFinding


class Environment(StrEnum):
    NEUTRAL = "neutral"
    KITCHEN = "kitchen"
    OFFICE = "office"


@dataclass(frozen=True)
class ScriptScene:
    environment: Environment
    evidence_phrase: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.environment, Environment):
            raise TypeError("scene environment must be a declared Environment")
        if self.environment is Environment.NEUTRAL:
            if self.evidence_phrase is not None:
                raise ValueError("neutral scene must not claim an evidence phrase")
        elif self.evidence_phrase is None or not self.evidence_phrase.strip():
            raise ValueError("specific scene requires an evidence phrase")

    def validate_text(self, authored_text: str) -> None:
        if self.evidence_phrase is None:
            return
        pattern = rf"(?<!\w){re.escape(self.evidence_phrase)}(?!\w)"
        if re.search(pattern, authored_text, flags=re.IGNORECASE) is None:
            raise ValueError("scene evidence phrase must occur in authored script")


@dataclass(frozen=True)
class EnvironmentEvidence:
    script_version_id: str
    avatar_version_id: str
    script_phrase: str
    required_environment: Environment
    avatar_environment: Environment


@dataclass(frozen=True)
class EnvironmentCheck:
    finding: QualityFinding | None
    evidence: EnvironmentEvidence | None


def check_script_avatar_compatibility(
    *,
    script_version_id: str,
    authored_text: str,
    scene: ScriptScene,
    avatar_version_id: str,
    avatar_environment: Environment,
) -> EnvironmentCheck:
    """Ground a mismatch in exact versions, text, and declared environments."""

    if scene.environment is Environment.NEUTRAL:
        return EnvironmentCheck(None, None)
    assert scene.evidence_phrase is not None
    phrase = scene.evidence_phrase
    scene.validate_text(authored_text)
    if avatar_environment in {scene.environment, Environment.NEUTRAL}:
        return EnvironmentCheck(None, None)
    evidence = EnvironmentEvidence(
        script_version_id=script_version_id,
        avatar_version_id=avatar_version_id,
        script_phrase=phrase,
        required_environment=scene.environment,
        avatar_environment=avatar_environment,
    )
    finding = QualityFinding(
        kind=FailureKind.ENVIRONMENT_MISMATCH,
        explanation=(
            f"{script_version_id} mentions {phrase!r} and requires "
            f"{scene.environment.value}; {avatar_version_id} is "
            f"{avatar_environment.value}."
        ),
        confidence=1.0,
    )
    return EnvironmentCheck(finding, evidence)
