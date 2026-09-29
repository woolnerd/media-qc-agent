"""Provider-neutral interpretation boundary; no workflow execution authority."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from media_qc_agent.domain.evidence import EvidenceInput


@dataclass(frozen=True)
class InterpretationRequest:
    """Untrusted review text and the exact versions/evidence available to it."""

    feedback: str
    artifact_version_ids: tuple[str, ...]
    evidence: tuple[EvidenceInput, ...] = ()

    def __post_init__(self) -> None:
        if not self.feedback.strip():
            raise ValueError("feedback must not be blank")
        if not self.artifact_version_ids or any(
            not version.strip() for version in self.artifact_version_ids
        ):
            raise ValueError("artifact versions must not be empty or blank")
        if len(set(self.artifact_version_ids)) != len(self.artifact_version_ids):
            raise ValueError("artifact versions must be unique")
        if any(
            item.artifact_version_id not in self.artifact_version_ids
            for item in self.evidence
        ):
            raise ValueError("evidence must reference a supplied artifact version")


class ModelProvider(Protocol):
    """Adapters own model selection, prompts and transport, returning raw JSON.

    Callers must validate output before treating it as a diagnosis. Providers
    cannot approve plans, mutate artifacts or submit paid generation jobs.
    """

    def interpret(self, request: InterpretationRequest) -> str: ...


class FakeModelProvider:
    """Exact fixture lookup, independent of call order and without networking.

    Deliberately supports malformed output fixtures for boundary tests. Missing
    requests fail explicitly instead of pretending to understand unseen text.
    """

    def __init__(self, responses: Mapping[InterpretationRequest, str]) -> None:
        self._responses = dict(responses)

    def interpret(self, request: InterpretationRequest) -> str:
        try:
            return self._responses[request]
        except KeyError:
            raise ValueError("no synthetic model response for request") from None
