"""Compare a rules-only workflow with model-assisted arms on held-out cases.

Replays recorded model outputs offline: every arm sees the same artifacts,
deterministic gates, repair policy, and human approval path. With `--live`,
records one paid request per case instead. See docs/agent/baseline-comparison.md.
"""

import argparse
import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from media_qc_agent.agent.contracts import InterpretationRequest, ModelIdentity
from media_qc_agent.agent.interpretation import DEFAULT_POLICY, AcceptancePolicy
from media_qc_agent.agent.openrouter import ModelProviderError, OpenRouterModelProvider
from media_qc_agent.agent.tracing import InterpretationTurn, interpret_traced
from media_qc_agent.cli.baseline_arms import ARMS, BASELINE_ARM, ArmDecision, Route
from media_qc_agent.cli.baseline_cases import (
    BASELINE_DATASET,
    BaselineCase,
    GateReport,
    interpretation_request,
    load_baseline_cases,
    run_gates,
)
from media_qc_agent.cli.baseline_metrics import (
    HUMAN_MINUTES,
    HUMAN_USD_PER_HOUR,
    MIN_MINUTES_REDUCTION,
    SAFETY_METRICS,
    VIDEO_JOB_USD,
    CaseScore,
    ModelUsage,
    paired_change,
    score_decision,
    totals,
    verdict,
)
from media_qc_agent.cli.compare import ReplayProvider, differences, request_digest

DEFAULT_RUNS = BASELINE_DATASET.parent / "results" / "baseline-runs-v1.json"
DEFAULT_EXPECTED = BASELINE_DATASET.parent / "results" / "baseline-comparison-v1.json"
_RUN_FIELDS = {"id", "provider", "model", "prompt_version", "cases"}
_CALL_FIELDS = {"request_sha256", "raw_output", "error", "elapsed_seconds", "cost_usd"}
BREAKDOWN_METRICS = (
    *SAFETY_METRICS,
    "false_blocks",
    "rejected_outputs",
    "diagnosis_rounds",
    "evidence_mismatches",
    "uncertainty_omissions",
    "human_minutes",
)


class MeteredProvider(Protocol):
    @property
    def identity(self) -> ModelIdentity: ...

    def interpret_metered(
        self, request: InterpretationRequest
    ) -> tuple[str, float | None]: ...


@dataclass(frozen=True)
class BaselineRun:
    """One recorded model call per case; a None output was a provider error."""

    id: str
    identity: ModelIdentity
    outputs: Mapping[str, str | None]
    usage: Mapping[str, ModelUsage]


@dataclass(frozen=True)
class ArmResult:
    decision: ArmDecision
    score: CaseScore
    usage: ModelUsage | None


def _call(
    provider: MeteredProvider,
    request: InterpretationRequest,
    clock: Callable[[], float],
) -> dict[str, Any]:
    started = clock()
    try:
        raw, cost = provider.interpret_metered(request)
        error = None
    except ModelProviderError as failure:
        raw, cost, error = None, None, str(failure)
    return {
        "request_sha256": request_digest(request),
        "raw_output": raw,
        "error": error,
        "elapsed_seconds": round(clock() - started, 3),
        "cost_usd": cost,
    }


def record_run(
    run_id: str,
    cases: tuple[BaselineCase, ...],
    provider: MeteredProvider,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """One request per case, no retries; errors are recorded, not raised."""

    return {
        "id": run_id,
        **asdict(provider.identity),
        "cases": {
            case.id: _call(
                provider, interpretation_request(case, run_gates(case)), clock
            )
            for case in cases
        },
    }


def _fields(value: Any, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("baseline run has missing or extra fields")
    return value


def _run(data: Any, cases: tuple[BaselineCase, ...]) -> BaselineRun:
    data = _fields(data, _RUN_FIELDS)
    if set(data["cases"]) != {case.id for case in cases}:
        raise ValueError(f"run {data['id']} must record every baseline case")
    outputs: dict[str, str | None] = {}
    usage = {}
    for case in cases:
        call = _fields(data["cases"][case.id], _CALL_FIELDS)
        digest = request_digest(interpretation_request(case, run_gates(case)))
        if call["request_sha256"] != digest:
            raise ValueError(f"case {case.id} differs from the request run saw")
        if (call["raw_output"] is None) == (call["error"] is None):
            raise ValueError("each call records exactly one of output or error")
        outputs[case.id] = call["raw_output"]
        usage[case.id] = ModelUsage(call["cost_usd"], call["elapsed_seconds"])
    identity = ModelIdentity(data["provider"], data["model"], data["prompt_version"])
    return BaselineRun(data["id"], identity, outputs, usage)


def load_runs(path: Path, cases: tuple[BaselineCase, ...]) -> tuple[BaselineRun, ...]:
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1 or not isinstance(data.get("runs"), list):
        raise ValueError("expected a version-1 baseline run file")
    runs = tuple(_run(item, cases) for item in data["runs"])
    if len({run.id for run in runs}) != len(runs):
        raise ValueError("baseline run IDs must be unique")
    return runs


def replay_turns(
    run: BaselineRun, cases: tuple[BaselineCase, ...], policy: AcceptancePolicy
) -> dict[str, InterpretationTurn]:
    """Recorded errors are absent from the replay map and become provider errors."""

    requests = {
        case.id: interpretation_request(case, run_gates(case)) for case in cases
    }
    responses = {
        requests[case_id]: raw
        for case_id, raw in run.outputs.items()
        if raw is not None
    }
    provider = ReplayProvider(run.identity, responses)
    return {
        case_id: interpret_traced(provider, request, policy=policy)
        for case_id, request in requests.items()
    }


def score_run(
    run: BaselineRun, cases: tuple[BaselineCase, ...], policy: AcceptancePolicy
) -> dict[str, dict[str, ArmResult]]:
    """Results by case, then arm; usage counts only when the arm called the model."""

    turns = replay_turns(run, cases, policy)
    results: dict[str, dict[str, ArmResult]] = {}
    for case in cases:
        gates: GateReport = run_gates(case)
        results[case.id] = {}
        for name, arm in ARMS.items():
            decision = arm(gates, turns[case.id])
            usage = run.usage[case.id] if decision.model_consulted else None
            results[case.id][name] = ArmResult(
                decision, score_decision(decision, case), usage
            )
    return results


def _record(result: ArmResult) -> dict[str, Any]:
    decision = result.decision
    flags = [
        name
        for name, value in asdict(result.score).items()
        if value and name not in HUMAN_MINUTES and name != "model_calls"
    ]
    return {
        "route": decision.route.value,
        "kind": decision.kind.value if decision.kind else None,
        "repairs": sorted(action.value for action in decision.scopes),
        "flags": flags,
    }


def _arm_totals(
    rows: Mapping[str, Mapping[str, ArmResult]], arm: str, case_ids: set[str]
) -> dict[str, float]:
    selected = [rows[case_id][arm] for case_id in rows if case_id in case_ids]
    return totals((r.score for r in selected), (r.usage for r in selected))


def _sum(items: Sequence[Mapping[str, float]]) -> dict[str, float]:
    return {key: round(sum(item[key] for item in items), 6) for key in items[0]}


def _breakdown(
    scored: list[dict[str, dict[str, ArmResult]]],
    groups: Mapping[str, set[str]],
    arm: str,
) -> dict[str, dict[str, float]]:
    report = {}
    for group, case_ids in sorted(groups.items()):
        summed = _sum([_arm_totals(rows, arm, case_ids) for rows in scored])
        report[group] = {name: summed[name] for name in BREAKDOWN_METRICS}
    return report


def _paired(
    run_ids: list[str], scored: list[dict[str, dict[str, ArmResult]]], arm: str
) -> dict[str, Any]:
    changes: dict[str, list[str]] = {"better": [], "same": [], "worse": []}
    for run_id, rows in zip(run_ids, scored, strict=True):
        for case_id, results in rows.items():
            change = paired_change(results[arm].score, results[BASELINE_ARM].score)
            changes[change].append(f"{run_id}/{case_id}")
    return {
        "better": len(changes["better"]),
        "same": len(changes["same"]),
        "worse": len(changes["worse"]),
        "better_cases": changes["better"],
        "worse_cases": changes["worse"],
    }


def _attribution(
    scored: list[dict[str, dict[str, ArmResult]]], arm_totals: Mapping[str, Any]
) -> dict[str, Any]:
    results = [results for rows in scored for results in rows.values()]

    def count(arm: str, test: Callable[[ArmResult], bool]) -> int:
        return sum(test(item[arm]) for item in results)

    def model_authority(item: ArmResult) -> bool:
        return item.decision.route is Route.MODEL

    def correct(item: ArmResult) -> bool:
        return model_authority(item) and not (
            item.score.false_passes or item.score.wrong_repairs
        )

    single, layered = arm_totals["single_shot"], arm_totals["layered"]
    return {
        "model_routing": {
            "compares": ["rules_only", "layered"],
            "model_authorized_decisions": count("layered", model_authority),
            "correct_model_authorized_decisions": count("layered", correct),
            "human_minutes_saved": round(
                arm_totals[BASELINE_ARM]["human_minutes"] - layered["human_minutes"], 6
            ),
        },
        "gate_precedence": {
            "compares": ["single_shot", "layered"],
            "safety_added_without_precedence": {
                name: round(single[name] - layered[name], 6) for name in SAFETY_METRICS
            },
            "human_minutes_added_without_precedence": round(
                single["human_minutes"] - layered["human_minutes"], 6
            ),
        },
        "clarification": {
            arm: {
                "creative_questions": count(arm, lambda r: r.decision.creative),
                "diagnostic_holds": int(arm_totals[arm]["diagnosis_rounds"]),
            }
            for arm in ARMS
        },
        "reassessment": {
            arm: {"human_re_reviews": int(arm_totals[arm]["re_review_rounds"])}
            for arm in ARMS
        },
        "evidence_selection": "not implemented: every arm gets the same evidence",
    }


def _protocol() -> dict[str, Any]:
    return {
        "human_minutes_per_round": HUMAN_MINUTES,
        "human_usd_per_hour": HUMAN_USD_PER_HOUR,
        "video_job_usd": VIDEO_JOB_USD,
        "min_human_minutes_reduction": MIN_MINUTES_REDUCTION,
        "safety_metrics": list(SAFETY_METRICS),
    }


def compare_arms(
    cases: tuple[BaselineCase, ...],
    runs: tuple[BaselineRun, ...],
    policy: AcceptancePolicy = DEFAULT_POLICY,
) -> dict[str, Any]:
    if not runs:
        raise ValueError("comparison needs at least one recorded run")
    all_ids = {case.id for case in cases}
    scored = [score_run(run, cases, policy) for run in runs]
    per_run = {
        arm: [_arm_totals(rows, arm, all_ids) for rows in scored] for arm in ARMS
    }
    arm_totals = {arm: _sum(per_run[arm]) for arm in ARMS}
    classes: dict[str, set[str]] = {}
    categories: dict[str, set[str]] = {}
    for case in cases:
        classes.setdefault(case.failure_class.value, set()).add(case.id)
        categories.setdefault(case.category, set()).add(case.id)
    run_ids = [run.id for run in runs]
    return {
        "dataset": BASELINE_DATASET.name,
        "policy_version": policy.version,
        "protocol": _protocol(),
        "runs": {
            run.id: {
                "model": asdict(run.identity),
                "totals": {arm: per_run[arm][index] for arm in ARMS},
                "cases": {
                    case_id: {arm: _record(result) for arm, result in results.items()}
                    for case_id, results in rows.items()
                },
            }
            for index, (run, rows) in enumerate(zip(runs, scored, strict=True))
        },
        "totals": arm_totals,
        "by_failure_class": {arm: _breakdown(scored, classes, arm) for arm in ARMS},
        "by_category": {arm: _breakdown(scored, categories, arm) for arm in ARMS},
        "paired_with_rules_only": {
            arm: _paired(run_ids, scored, arm) for arm in ARMS if arm != BASELINE_ARM
        },
        "verdicts": {
            arm: verdict(per_run[arm], per_run[BASELINE_ARM])
            for arm in ARMS
            if arm != BASELINE_ARM
        },
        "attribution": _attribution(scored, arm_totals),
    }


def _append_run(path: Path, run: dict[str, Any]) -> None:
    data: dict[str, Any] = {
        "schema_version": 1,
        "dataset": BASELINE_DATASET.name,
        "runs": [],
    }
    if path.exists():
        data = json.loads(path.read_text())
    if any(item["id"] == run["id"] for item in data["runs"]):
        raise ValueError(f"run {run['id']} is already recorded")
    data["runs"].append(run)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=BASELINE_DATASET)
    parser.add_argument("--runs", type=Path, default=DEFAULT_RUNS)
    parser.add_argument(
        "--expect", type=Path, help="Saved comparison; exit nonzero on any difference"
    )
    parser.add_argument(
        "--live",
        metavar="RUN_ID",
        help="Make one paid OpenRouter request per case and append the run",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    cases = load_baseline_cases(args.dataset)
    if args.live:
        run = record_run(args.live, cases, OpenRouterModelProvider.from_environment())
        _append_run(args.runs, run)
        print(f"recorded run {args.live} in {args.runs}")
        return
    report = compare_arms(cases, load_runs(args.runs, cases))
    print(json.dumps(report, indent=2))
    if args.expect is None:
        return
    failures = differences(json.loads(args.expect.read_text()), report)
    for line in failures:
        print(line, file=sys.stderr)
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
