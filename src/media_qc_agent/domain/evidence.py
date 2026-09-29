"""Immutable, artifact-bound quality findings and evidence records."""

from dataclasses import dataclass
from enum import StrEnum

from media_qc_agent.domain.models import FailureKind


class EvidenceRole(StrEnum):
    FACT = "fact"
    INFERENCE = "inference"
    UNCERTAINTY = "uncertainty"


@dataclass(frozen=True)
class EvidenceInput:
    role: EvidenceRole
    artifact_version_id: str
    statement: str
    observed: str | None = None
    limit: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.role, EvidenceRole):
            raise TypeError("evidence role must be fact, inference, or uncertainty")
        if not self.artifact_version_id.strip():
            raise ValueError("evidence artifact version ID must not be blank")
        if not self.statement.strip():
            raise ValueError("evidence statement must not be blank")


@dataclass(frozen=True)
class QualityFindingRecord:
    id: str
    run_id: str
    artifact_version_id: str
    kind: FailureKind
    explanation: str
    confidence: float
    created_at: str


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    finding_id: str
    role: EvidenceRole
    artifact_version_id: str
    statement: str
    observed: str | None
    limit: str | None
    created_at: str
