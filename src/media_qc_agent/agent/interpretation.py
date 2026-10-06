"""Strict model-output validation followed by deterministic repair policy."""

import json
import math
from dataclasses import dataclass
from typing import Any

from media_qc_agent.agent.contracts import InterpretationRequest, ModelProvider
from media_qc_agent.domain.evidence import EvidenceInput, EvidenceRole
from media_qc_agent.domain.models import (
    ArtifactKind,
    ClarificationRequest,
    FailureKind,
    QualityFinding,
    RepairAction,
    RepairPlan,
)
from media_qc_agent.domain.planner import plan_repair

MIN_CONFIDENCE = 0.8
_FIELDS = {
    "kind",
    "explanation",
    "confidence",
    "evidence_indices",
    "action",
    "invalidates",
}


@dataclass(frozen=True)
class Interpretation:
    finding: QualityFinding | None
    decision: RepairPlan | ClarificationRequest | None
    evidence: tuple[EvidenceInput, ...]
    clarification: str | None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate output field: {key}")
        result[key] = value
    return result


def _read_output(raw: str) -> dict[str, Any]:
    try:
        output = json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValueError("model output must be a JSON object") from error
    if not isinstance(output, dict) or set(output) != _FIELDS:
        raise ValueError("model output must contain exactly the classification fields")
    if not isinstance(output["explanation"], str) or not output["explanation"].strip():
        raise ValueError("explanation must be a nonblank string")
    confidence = output["confidence"]
    if type(confidence) not in {int, float} or not math.isfinite(confidence):
        raise ValueError("confidence must be a finite number")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between zero and one")
    return output


def _evidence(
    indices: Any, request: InterpretationRequest
) -> tuple[EvidenceInput, ...]:
    if not isinstance(indices, list) or any(type(i) is not int for i in indices):
        raise ValueError("evidence_indices must be an integer array")
    if len(set(indices)) != len(indices):
        raise ValueError("evidence indices must be unique")
    if any(i < 0 or i >= len(request.evidence) for i in indices):
        raise ValueError("evidence index is outside supplied evidence")
    return tuple(request.evidence[i] for i in indices)


def _proposal(
    output: dict[str, Any],
) -> tuple[RepairAction | None, frozenset[ArtifactKind]]:
    action = output["action"]
    if action is not None and not isinstance(action, str):
        raise ValueError("action must be a string or null")
    invalidates = output["invalidates"]
    if not isinstance(invalidates, list) or any(
        not isinstance(item, str) for item in invalidates
    ):
        raise ValueError("invalidates must be a string array")
    if len(set(invalidates)) != len(invalidates):
        raise ValueError("invalidations must be unique")
    return (
        RepairAction(action) if action is not None else None,
        frozenset(ArtifactKind(item) for item in invalidates),
    )


def _validate_policy(
    decision: RepairPlan | ClarificationRequest,
    action: RepairAction | None,
    invalidates: frozenset[ArtifactKind],
) -> None:
    if isinstance(decision, ClarificationRequest):
        if action is not None or invalidates:
            raise ValueError("model cannot choose an unresolved creative repair")
    elif action is not decision.action or invalidates != decision.invalidates:
        raise ValueError("model proposal violates minimum-repair policy")


def validate_interpretation(raw: str, request: InterpretationRequest) -> Interpretation:
    """Accept only known diagnoses grounded in supplied facts, without side effects.

    Evidence citations identify supplied records, never model-invented facts.
    This validates structure and scope, not the semantic truth of a diagnosis.
    """

    output = _read_output(raw)
    evidence = _evidence(output["evidence_indices"], request)
    action, invalidates = _proposal(output)
    kind = output["kind"]
    if kind is None:
        if action is not None or invalidates:
            raise ValueError("uncertain diagnosis cannot propose repair")
        return Interpretation(None, None, evidence, output["explanation"])
    if not isinstance(kind, str):
        raise ValueError("kind must be a supported diagnosis or null")  # noqa: TRY004
    finding = QualityFinding(
        FailureKind(kind), output["explanation"], output["confidence"]
    )
    decision = plan_repair(finding)
    _validate_policy(decision, action, invalidates)
    if finding.confidence < MIN_CONFIDENCE or not any(
        item.role is EvidenceRole.FACT for item in evidence
    ):
        return Interpretation(
            None,
            None,
            evidence,
            "Please provide artifact evidence or clarify the diagnosis.",
        )
    return Interpretation(finding, decision, evidence, None)


def repair_scopes(
    result: Interpretation,
) -> dict[RepairAction, frozenset[ArtifactKind]]:
    """Every repair the decision would allow, including offered creative branches."""

    if isinstance(result.decision, RepairPlan):
        return {result.decision.action: result.decision.invalidates}
    if isinstance(result.decision, ClarificationRequest):
        return {option.action: option.invalidates for option in result.decision.options}
    return {}


def clarification_type(result: Interpretation) -> str:
    if result.clarification is not None:
        return "diagnostic"
    return "creative" if isinstance(result.decision, ClarificationRequest) else "none"


def interpret_feedback(
    provider: ModelProvider, request: InterpretationRequest
) -> Interpretation:
    return validate_interpretation(provider.interpret(request), request)
