"""Replay saved interpretation cases against fake or live model providers."""

import argparse
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Tracer

from media_qc_agent.agent.contracts import (
    FakeModelProvider,
    InterpretationRequest,
    ModelProvider,
)
from media_qc_agent.agent.interpretation import (
    Interpretation,
    clarification_type,
    repair_scopes,
)
from media_qc_agent.agent.jev import JevModelProvider
from media_qc_agent.agent.openrouter import OpenRouterModelProvider
from media_qc_agent.agent.tracing import (
    InterpretationTurn,
    annotate,
    interpret_traced,
    safe_span,
)
from media_qc_agent.domain.evidence import EvidenceInput, EvidenceRole
from media_qc_agent.domain.ids import validate_artifact_version_id, validate_external_id
from media_qc_agent.domain.models import ArtifactKind, FailureKind, RepairAction

DEFAULT_DATASET = Path(__file__).resolve().parents[3] / "evals" / "agent-cases-v1.json"
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
    turn: InterpretationTurn | None = None

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


def mask_version_labels(case: EvaluationCase) -> EvaluationCase:
    """Remove taxonomy/difficulty hints from synthetic version names for comparison.

    Keep artifact kind prefixes and all evidence text, roles and lineage links.
    This is evaluation-only; never persist these masked versions as a repair.
    """

    ids = {
        version: "video:benchmark"
        if version.startswith("video:")
        else version.split("-", 1)[0] + "-benchmark"
        for version in case.request.artifact_version_ids
    }
    request = replace(
        case.request,
        artifact_version_ids=tuple(
            ids[version] for version in case.request.artifact_version_ids
        ),
        evidence=tuple(
            replace(item, artifact_version_id=ids[item.artifact_version_id])
            for item in case.request.evidence
        ),
    )
    return replace(case, request=request)


def score_interpretation(case: EvaluationCase, result: Interpretation) -> CaseResult:
    expected = case.expected
    failures = []
    if (result.finding.kind if result.finding else None) != expected.kind:
        failures.append("classification")
    if set(result.evidence) != {
        case.request.evidence[i] for i in expected.evidence_indices
    }:
        failures.append("evidence")
    if clarification_type(result) != expected.clarification:
        failures.append("clarification")
    scopes = repair_scopes(result)
    if (
        scopes != dict(expected.invalidations_by_action)
        or set(scopes) & expected.forbidden_actions
    ):
        failures.append("repair_scope")
    return CaseResult(case.id, tuple(failures))


def score_turn(case: EvaluationCase, turn: InterpretationTurn) -> CaseResult:
    """Keep failure labels stable so new runs compare with saved results."""

    if turn.interpretation is None:
        return CaseResult(case.id, ("provider_or_validation_error",), turn)
    return replace(score_interpretation(case, turn.interpretation), turn=turn)


def evaluate_case(
    case: EvaluationCase, provider: ModelProvider, *, tracer: Tracer | None = None
) -> CaseResult:
    """Score one traced turn; the score is recorded on the enclosing case span."""

    tracer = tracer or trace.get_tracer("media_qc_agent.evaluation")
    with safe_span(tracer, "eval.case") as span:
        result = score_turn(
            case, interpret_traced(provider, case.request, tracer=tracer)
        )
        annotate(
            span,
            {
                "eval.case_id": case.id,
                "eval.category": case.category,
                "eval.passed": result.passed,
                "eval.failures": result.failures,
            },
        )
    return result


def _interpretation_record(
    request: InterpretationRequest, interpretation: Interpretation | None
) -> dict[str, Any] | None:
    if interpretation is None:
        return None
    finding = interpretation.finding
    return {
        "kind": finding.kind.value if finding else None,
        "clarification": clarification_type(interpretation),
        "cited_evidence_indices": [
            request.evidence.index(item) for item in interpretation.evidence
        ],
        "repair_scopes": {
            action.value: sorted(kind.value for kind in kinds)
            for action, kinds in sorted(repair_scopes(interpretation).items())
        },
    }


def trace_record(case: EvaluationCase, result: CaseResult) -> dict[str, Any]:
    """Full synthetic inputs and outputs for one case; never for real review data.

    `trace_id` joins the record to exported spans when a tracer SDK is configured.
    """

    turn = result.turn
    if turn is None:
        raise ValueError("case result has no recorded interpretation turn")
    return {
        "case_id": case.id,
        "category": case.category,
        "failure_class": case.failure_class.value,
        "model": asdict(turn.identity),
        "trace_id": turn.trace_id,
        "artifact_version_ids": list(turn.request.artifact_version_ids),
        "input": {
            "feedback": turn.request.feedback,
            "evidence": [asdict(item) for item in turn.request.evidence],
        },
        "raw_output": turn.raw_output,
        "outcome": turn.outcome.value,
        "interpretation": _interpretation_record(turn.request, turn.interpretation),
        "score": {"passed": result.passed, "failures": list(result.failures)},
    }


def write_traces(
    path: Path, cases: tuple[EvaluationCase, ...], results: tuple[CaseResult, ...]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = (
        json.dumps(trace_record(case, result))
        for case, result in zip(cases, results, strict=True)
    )
    path.write_text("".join(f"{line}\n" for line in lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--case-id", help="Replay only one saved case")
    parser.add_argument(
        "--mask-version-labels",
        action="store_true",
        help="Remove class/difficulty hints from synthetic version IDs",
    )
    parser.add_argument(
        "--provider",
        choices=("chat", "jev"),
        default="chat",
        help="Provider used for --live requests",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Make a paid OpenRouter request per selected case",
    )
    parser.add_argument(
        "--traces",
        type=Path,
        help="Write one JSONL trace record per case (synthetic data only)",
    )
    args = parser.parse_args()
    cases = load_cases(args.dataset)
    if args.mask_version_labels:
        cases = tuple(mask_version_labels(case) for case in cases)
    if args.case_id:
        cases = tuple(case for case in cases if case.id == args.case_id)
        if not cases:
            parser.error("case ID was not found")
    provider = _evaluation_provider(cases, live=args.live, provider_name=args.provider)
    results = tuple(evaluate_case(case, provider) for case in cases)
    if args.traces:
        write_traces(args.traces, cases, results)
    print(
        json.dumps(
            {
                "mode": "live" if args.live else "fixture",
                "provider": args.provider if args.live else "fake",
                "model": asdict(provider.identity),
                "traces": str(args.traces) if args.traces else None,
                "version_labels_masked": args.mask_version_labels,
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


def _evaluation_provider(
    cases: tuple[EvaluationCase, ...], *, live: bool, provider_name: str
) -> ModelProvider:
    if not live:
        return fixture_provider(cases)
    if provider_name == "jev":
        return JevModelProvider.from_environment()
    return OpenRouterModelProvider.from_environment()


if __name__ == "__main__":
    main()
