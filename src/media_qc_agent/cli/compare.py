"""Compare acceptance policies by replaying saved model outputs offline.

Every recorded run and the saved fixture responses are scored under each policy
version. False passes (wrong output given repair authority) are counted
separately from false blocks (a correct or absent proposal held on a
repair-worthy case) and blocked wrong outputs (a wrong proposal held).
With `--expect`, any difference from the saved comparison exits nonzero.
"""

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from media_qc_agent.agent.contracts import (
    FakeModelProvider,
    InterpretationRequest,
    ModelIdentity,
)
from media_qc_agent.agent.interpretation import (
    DEFAULT_POLICY,
    POLICIES,
    AcceptancePolicy,
)
from media_qc_agent.agent.tracing import InterpretationTurn, TurnOutcome
from media_qc_agent.cli.evaluate import (
    DEFAULT_DATASET,
    CaseResult,
    EvaluationCase,
    evaluate_case,
    exact_fields,
    load_cases,
    mask_version_labels,
)

EVALS = DEFAULT_DATASET.parent
DEFAULT_RECORDINGS = EVALS / "recorded-outputs-v1.json"
DEFAULT_EXPECTED = EVALS / "results" / "policy-comparison-v1.json"
FIXTURE_RUN = "fixture"
_RUN_FIELDS = {
    "id",
    "source",
    "provider",
    "model",
    "prompt_version",
    "version_labels_masked",
    "cases",
}
_MISSING = "<missing>"


@dataclass(frozen=True)
class RecordedRun:
    """Raw outputs saved from one model run, replayable without the network."""

    id: str
    identity: ModelIdentity
    version_labels_masked: bool
    outputs: Mapping[str, str]
    recorded_failures: Mapping[str, tuple[str, ...]]
    request_digests: Mapping[str, str]


def request_digest(request: InterpretationRequest) -> str:
    """Binds a recorded output to the exact request text and versions it answered."""

    content = {
        "feedback": request.feedback,
        "artifact_version_ids": request.artifact_version_ids,
        "evidence": [asdict(item) for item in request.evidence],
    }
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class ReplayProvider(FakeModelProvider):
    """Exact request lookup reporting the recorded run's model identity."""

    def __init__(
        self, identity: ModelIdentity, responses: Mapping[InterpretationRequest, str]
    ) -> None:
        super().__init__(responses)
        self.identity = identity


def _run(data: Any, case_ids: set[str]) -> RecordedRun:
    data = exact_fields(data, _RUN_FIELDS, "recorded run")
    if set(data["cases"]) != case_ids:
        raise ValueError(f"run {data['id']} must record every dataset case")
    return RecordedRun(
        data["id"],
        # September runs predate content-hashed prompt versions: "unrecorded".
        ModelIdentity(data["provider"], data["model"], data["prompt_version"]),
        data["version_labels_masked"] is True,
        {key: json.dumps(case["output"]) for key, case in data["cases"].items()},
        {key: tuple(case["failures"]) for key, case in data["cases"].items()},
        {key: case["request_sha256"] for key, case in data["cases"].items()},
    )


def load_recordings(
    path: Path, cases: tuple[EvaluationCase, ...]
) -> tuple[RecordedRun, ...]:
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1 or not data.get("runs"):
        raise ValueError("expected a nonempty version-1 recording file")
    runs = tuple(_run(item, {case.id for case in cases}) for item in data["runs"])
    if len({run.id for run in runs} | {FIXTURE_RUN}) != len(runs) + 1:
        raise ValueError("recorded run IDs must be unique and not 'fixture'")
    return runs


def fixture_run(cases: tuple[EvaluationCase, ...]) -> RecordedRun:
    """Saved safe responses: wiring that must pass under every policy."""

    return RecordedRun(
        FIXTURE_RUN,
        FakeModelProvider.identity,
        False,
        {case.id: case.fake_response for case in cases},
        {case.id: () for case in cases},
        {case.id: request_digest(case.request) for case in cases},
    )


def error_type(case: EvaluationCase, result: CaseResult) -> str | None:
    """Pure classification of a scored case for policy comparison."""

    if result.passed:
        return None
    interpretation = result.turn.interpretation if result.turn else None
    if interpretation is not None and interpretation.finding is not None:
        return "false_pass"
    if case.expected.kind is None:
        return "other_failure"
    if _proposed_wrong(case.expected.kind.value, result.turn):
        return "blocked_wrong_output"
    return "false_block"


def _proposed_wrong(expected_kind: str, turn: InterpretationTurn | None) -> bool:
    """Whether a held turn's raw output proposed something other than the answer."""

    if turn is None or turn.outcome is TurnOutcome.PROVIDER_ERROR:
        return False
    if turn.outcome is TurnOutcome.REJECTED:
        return True
    # Abstained turns passed validation, so their raw output is a JSON object.
    proposed = json.loads(turn.raw_output or "{}").get("kind")
    return proposed is not None and proposed != expected_kind


def _replay_responses(
    run: RecordedRun, cases: tuple[EvaluationCase, ...]
) -> dict[InterpretationRequest, str]:
    responses = {}
    for case in cases:
        if request_digest(case.request) != run.request_digests[case.id]:
            raise ValueError(
                f"case {case.id} differs from the request run {run.id} saw"
            )
        responses[case.request] = run.outputs[case.id]
    if len(responses) != len(cases):
        raise ValueError("each replayed case needs a unique request")
    return responses


def replay(
    run: RecordedRun, cases: tuple[EvaluationCase, ...], policy: AcceptancePolicy
) -> tuple[CaseResult, ...]:
    if run.version_labels_masked:
        cases = tuple(mask_version_labels(case) for case in cases)
    provider = ReplayProvider(run.identity, _replay_responses(run, cases))
    return tuple(evaluate_case(case, provider, policy=policy) for case in cases)


def summarize(
    cases: tuple[EvaluationCase, ...], results: tuple[CaseResult, ...]
) -> dict[str, Any]:
    errors = {
        case.id: {"type": kind, "failures": list(result.failures)}
        for case, result in zip(cases, results, strict=True)
        if (kind := error_type(case, result)) is not None
    }
    types = [error["type"] for error in errors.values()]
    return {
        "passed": len(results) - len(errors),
        "total": len(results),
        "false_passes": types.count("false_pass"),
        "false_blocks": types.count("false_block"),
        "blocked_wrong_outputs": types.count("blocked_wrong_output"),
        "other_failures": types.count("other_failure"),
        "errors": errors,
    }


def _totals(summaries: list[dict[str, Any]]) -> dict[str, int]:
    keys = (
        "passed",
        "total",
        "false_passes",
        "false_blocks",
        "blocked_wrong_outputs",
        "other_failures",
    )
    return {key: sum(summary[key] for summary in summaries) for key in keys}


def compare(
    cases: tuple[EvaluationCase, ...],
    runs: tuple[RecordedRun, ...],
    policies: tuple[AcceptancePolicy, ...],
) -> dict[str, Any]:
    by_run = {
        run.id: {p.version: summarize(cases, replay(run, cases, p)) for p in policies}
        for run in runs
    }
    return {
        "default_policy": DEFAULT_POLICY.version,
        "policies": {policy.version: asdict(policy) for policy in policies},
        "runs": {
            run.id: {"model": asdict(run.identity), "by_policy": by_run[run.id]}
            for run in runs
        },
        "totals": {
            p.version: _totals([by_run[run.id][p.version] for run in runs])
            for p in policies
        },
    }


def differences(expected: Any, actual: Any, path: str = "") -> list[str]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        return [
            line
            for key in sorted(set(expected) | set(actual))
            for line in differences(
                expected.get(key, _MISSING),
                actual.get(key, _MISSING),
                f"{path}.{key}" if path else key,
            )
        ]
    if expected == actual:
        return []
    return [f"{path}: expected {json.dumps(expected)}, got {json.dumps(actual)}"]


def gate_failures(
    report: dict[str, Any], expected: Any, *, check_snapshot: bool
) -> list[str]:
    """Fixture replay must always pass; recorded runs must match the snapshot."""

    failures = [
        f"fixture fails under {version}"
        for version, summary in report["runs"][FIXTURE_RUN]["by_policy"].items()
        if summary["passed"] != summary["total"]
    ]
    if not check_snapshot:
        return failures
    if not isinstance(expected, dict):
        return [*failures, "expected snapshot must be a JSON object"]
    return failures + differences(expected, report)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--recordings", type=Path, default=DEFAULT_RECORDINGS)
    parser.add_argument(
        "--expect",
        type=Path,
        help="Saved comparison; exit nonzero on any difference",
    )
    args = parser.parse_args()
    cases = load_cases(args.dataset)
    runs = (fixture_run(cases), *load_recordings(args.recordings, cases))
    report = compare(cases, runs, tuple(POLICIES.values()))
    print(json.dumps(report, indent=2))
    check = args.expect is not None
    expected = json.loads(args.expect.read_text()) if check else None
    failures = gate_failures(report, expected, check_snapshot=check)
    for line in failures:
        print(line, file=sys.stderr)
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
