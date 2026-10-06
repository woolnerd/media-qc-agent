"""Provider-neutral interpretation boundary; no workflow execution authority."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from media_qc_agent.domain.evidence import EvidenceInput


def prompt_version(family: str, content: Any) -> str:
    """Derive a version from prompt content so edits cannot reuse an old label.

    Content must be JSON-serializable. Key order does not affect the version.
    """

    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return f"{family}:{hashlib.sha256(canonical.encode()).hexdigest()[:12]}"


@dataclass(frozen=True)
class ModelIdentity:
    """Which adapter, model, and prompt produced an output; compared separately."""

    provider: str
    model: str
    prompt_version: str

    def __post_init__(self) -> None:
        if any(
            not part.strip()
            for part in (self.provider, self.model, self.prompt_version)
        ):
            raise ValueError("model identity parts must not be blank")


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

    @property
    def identity(self) -> ModelIdentity: ...

    def interpret(self, request: InterpretationRequest) -> str: ...


class FakeModelProvider:
    """Exact fixture lookup, independent of call order and without networking.

    Deliberately supports malformed output fixtures for boundary tests. Missing
    requests fail explicitly instead of pretending to understand unseen text.
    """

    identity = ModelIdentity("fake", "fixture", "fixture")

    def __init__(self, responses: Mapping[InterpretationRequest, str]) -> None:
        self._responses = dict(responses)

    def interpret(self, request: InterpretationRequest) -> str:
        try:
            return self._responses[request]
        except KeyError:
            raise ValueError("no synthetic model response for request") from None
