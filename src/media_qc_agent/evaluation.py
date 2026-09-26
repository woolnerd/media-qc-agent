"""Replay saved interpretation cases against fake or live model providers."""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import (
    ArtifactKind,
    ClarificationRequest,
    FailureKind,
    RepairAction,
    RepairPlan,
)
from .ids import validate_artifact_version_id, validate_external_id
from .interpretation import Interpretation, interpret_feedback
from .model import FakeModelProvider, InterpretationRequest, ModelProvider
from .openrouter import ModelProviderError, OpenRouterModelProvider
from .quality_records import EvidenceInput, EvidenceRole

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "evals" / "agent-cases-v1.json"
_CATEGORIES = {"clear", "ambiguous", "adversarial"}


@dataclass(frozen=True)
class CaseExpectation:
    kind: FailureKind | None
    evidence_indices: tuple[int, ...]
    clarification: str
    allowed_actions: frozenset[RepairAction]
    forbidden_actions: frozenset[RepairAction]
    invalidations_by_action: tuple[tuple[RepairAction, frozenset[ArtifactKind]], ...]


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    failure_class: FailureKind
    category: str
    request: InterpretationRequest
    expected: CaseExpectation
    fake_response: str


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures


def _fields(value: Any, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("dataset object has missing or extra fields")
    return value


def _input_versions(data: dict[str, Any]) -> tuple[str, ...]:
    versions = _fields(data, {kind.value for kind in ArtifactKind})
    for kind in ArtifactKind:
        version = versions[kind.value]
        if not isinstance(version, str):
            raise ValueError("input version ID must be a string")  # noqa: TRY004
        if kind is ArtifactKind.VIDEO:
            if not version.startswith("video:"):
                raise ValueError("video version ID must use video:<job-id>")
            validate_external_id(version.removeprefix("video:"), "video fixture")
        else:
            validate_artifact_version_id(version, kind)
    return tuple(versions.values())


def _expectation(
    data: dict[str, Any], request: InterpretationRequest
) -> CaseExpectation:
    data = _fields(
        data,
        {
            "kind",
            "evidence_indices",
            "clarification",
            "allowed_actions",
            "forbidden_actions",
            "invalidations_by_action",
        },
    )
    indices = data["evidence_indices"]
    if not isinstance(indices, list) or any(
        type(i) is not int or not 0 <= i < len(request.evidence) for i in indices
    ):
        raise ValueError("expected evidence indices are invalid")
    if data["clarification"] not in {"none", "diagnostic", "creative"}:
        raise ValueError("unknown clarification expectation")
    allowed = frozenset(RepairAction(a) for a in data["allowed_actions"])
    forbidden = frozenset(RepairAction(a) for a in data["forbidden_actions"])
    if allowed & forbidden or allowed | forbidden != frozenset(RepairAction):
        raise ValueError("allowed/forbidden actions must partition all repair actions")
    invalidations = tuple(
        (RepairAction(a), frozenset(ArtifactKind(k) for k in kinds))
        for a, kinds in data["invalidations_by_action"].items()
    )
    if {action for action, _ in invalidations} != allowed:
        raise ValueError("each allowed action needs exact invalidations")
    return CaseExpectation(
        FailureKind(data["kind"]) if data["kind"] else None,
        tuple(indices),
        data["clarification"],
        allowed,
        forbidden,
        invalidations,
    )


def _case(data: dict[str, Any]) -> EvaluationCase:
    data = _fields(
        data,
        {
            "id",
            "failure_class",
            "category",
            "input_versions",
            "fixture_context",
            "feedback",
            "evidence",
            "expected",
            "fake_response",
        },
    )
    if not isinstance(data["id"], str) or not data["id"].strip():
        raise ValueError("case ID must be a nonblank string")
    if data["category"] not in _CATEGORIES:
        raise ValueError("unknown case category")
    versions = _input_versions(data["input_versions"])
    evidence = tuple(
        EvidenceInput(
            EvidenceRole(item["role"]),
            item["artifact_version_id"],
            item["statement"],
            item["observed"],
            item["limit"],
        )
        for item in data["evidence"]
    )
    request = InterpretationRequest(data["feedback"], versions, evidence)
    return EvaluationCase(
        data["id"],
        FailureKind(data["failure_class"]),
        data["category"],
        request,
        _expectation(data["expected"], request),
        json.dumps(data["fake_response"]),
    )


def load_cases(path: Path = DEFAULT_DATASET) -> tuple[EvaluationCase, ...]:
    data = _fields(
        json.loads(path.read_text()), {"schema_version", "description", "cases"}
    )
    if (
        data["schema_version"] != 1
        or not isinstance(data["cases"], list)
        or not data["cases"]
    ):
        raise ValueError("expected a nonempty version-1 dataset")
    cases = tuple(_case(item) for item in data["cases"])
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("case IDs must be unique")
    return cases


def fixture_provider(cases: tuple[EvaluationCase, ...]) -> FakeModelProvider:
    return FakeModelProvider({case.request: case.fake_response for case in cases})


def _repair_scopes(
    result: Interpretation,
) -> dict[RepairAction, frozenset[ArtifactKind]]:
    if isinstance(result.decision, RepairPlan):
        return {result.decision.action: result.decision.invalidates}
    if isinstance(result.decision, ClarificationRequest):
        return {option.action: option.invalidates for option in result.decision.options}
    return {}


def _clarification_type(result: Interpretation) -> str:
    if result.clarification is not None:
        return "diagnostic"
    return "creative" if isinstance(result.decision, ClarificationRequest) else "none"


def score_interpretation(case: EvaluationCase, result: Interpretation) -> CaseResult:
    expected = case.expected
    failures = []
    if (result.finding.kind if result.finding else None) != expected.kind:
        failures.append("classification")
    if set(result.evidence) != {
        case.request.evidence[i] for i in expected.evidence_indices
    }:
        failures.append("evidence")
    if _clarification_type(result) != expected.clarification:
        failures.append("clarification")
    scopes = _repair_scopes(result)
    if (
        scopes != dict(expected.invalidations_by_action)
        or set(scopes) & expected.forbidden_actions
    ):
        failures.append("repair_scope")
    return CaseResult(case.id, tuple(failures))


def evaluate_case(case: EvaluationCase, provider: ModelProvider) -> CaseResult:
    try:
        result = interpret_feedback(provider, case.request)
    except (ValueError, ModelProviderError):
        return CaseResult(case.id, ("provider_or_validation_error",))
    return score_interpretation(case, result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--case-id", help="Replay only one saved case")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Make a paid OpenRouter request per selected case",
    )
    args = parser.parse_args()
    cases = load_cases(args.dataset)
    if args.case_id:
        cases = tuple(case for case in cases if case.id == args.case_id)
        if not cases:
            parser.error("case ID was not found")
    provider: ModelProvider = (
        OpenRouterModelProvider.from_environment()
        if args.live
        else fixture_provider(cases)
    )
    results = tuple(evaluate_case(case, provider) for case in cases)
    print(
        json.dumps(
            {
                "mode": "live" if args.live else "fixture",
                "passed": sum(result.passed for result in results),
                "total": len(results),
                "results": [
                    {"id": r.case_id, "passed": r.passed, "failures": r.failures}
                    for r in results
                ],
            },
            indent=2,
        )
    )
    raise SystemExit(0 if all(result.passed for result in results) else 1)


if __name__ == "__main__":
    main()
