import io
import json
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from media_qc_agent.agent.contracts import InterpretationRequest, ModelIdentity
from media_qc_agent.agent.interpretation import DEFAULT_POLICY
from media_qc_agent.agent.openrouter import ModelProviderError
from media_qc_agent.cli.baseline import (
    BaselineRun,
    compare_arms,
    load_runs,
    main,
    record_run,
    score_run,
)
from media_qc_agent.cli.baseline_arms import Route
from media_qc_agent.cli.baseline_cases import (
    BASELINE_DATASET,
    BaselineCase,
    HumanDecision,
    PostRepairCheck,
    interpretation_request,
    load_baseline_cases,
    run_gates,
)
from media_qc_agent.cli.baseline_metrics import (
    SAFETY_METRICS,
    CaseScore,
    ModelUsage,
    paired_change,
    verdict,
)
from media_qc_agent.domain.evidence import EvidenceRole
from media_qc_agent.domain.ids import artifact_kind
from media_qc_agent.domain.models import (
    ClarificationRequest,
    FailureKind,
    QualityFinding,
)
from media_qc_agent.domain.planner import plan_repair

CASES = load_baseline_cases()
BY_ID = {case.id: case for case in CASES}
IDENTITY = ModelIdentity("test", "oracle", "test:1")


def _cited(case: BaselineCase, request: InterpretationRequest) -> list[int]:
    role = EvidenceRole.UNCERTAINTY if case.truth.defect is None else EvidenceRole.FACT
    return [
        index
        for index, item in enumerate(request.evidence)
        if item.role is role
        and (
            case.truth.defect is None
            or artifact_kind(item.artifact_version_id) in case.truth.evidence_artifacts
        )
    ]


def oracle_output(case: BaselineCase, kind: FailureKind | None = None) -> str:
    """A policy-valid answer for `kind`, defaulting to the ground truth."""

    kind = case.truth.defect if kind is None else kind
    request = interpretation_request(case, run_gates(case))
    action, invalidates = None, []
    if kind is not None:
        decision = plan_repair(QualityFinding(kind, "oracle", 1.0))
        if not isinstance(decision, ClarificationRequest):
            action = decision.action.value
            invalidates = sorted(k.value for k in decision.invalidates)
    return json.dumps(
        {
            "kind": kind.value if kind else None,
            "explanation": "Synthetic test answer.",
            "confidence": 0.95 if kind else 0.2,
            "evidence_indices": _cited(case, request),
            "action": action,
            "invalidates": invalidates,
        }
    )


def recorded(outputs: dict[str, str | None], run_id: str = "r1") -> BaselineRun:
    return BaselineRun(
        run_id,
        IDENTITY,
        {case.id: outputs.get(case.id, oracle_output(case)) for case in CASES},
        {case.id: ModelUsage(0.0001, 1.0) for case in CASES},
    )


class DatasetTests(unittest.TestCase):
    def test_covers_every_class_with_clear_ambiguous_and_adversarial_cases(
        self,
    ) -> None:
        coverage = Counter((case.failure_class, case.category) for case in CASES)
        for kind in FailureKind:
            for category in ("clear", "ambiguous", "adversarial"):
                with self.subTest(kind=kind, category=category):
                    self.assertGreaterEqual(coverage[kind, category], 1)

    def test_gate_annotations_match_deterministic_gates(self) -> None:
        for case in CASES:
            with self.subTest(case=case.id):
                finding = run_gates(case).finding
                if case.gate_detects:
                    assert finding is not None
                    self.assertIs(finding.kind, case.failure_class)
                    self.assertIs(case.truth.post_repair_check, PostRepairCheck.GATE)
                else:
                    self.assertIsNone(finding)

    def test_ground_truth_repairs_agree_with_minimum_repair_policy(self) -> None:
        for case in CASES:
            truth = case.truth
            with self.subTest(case=case.id):
                if truth.defect is None:
                    self.assertIs(truth.human_decision, HumanDecision.DIAGNOSE)
                    continue
                decision = plan_repair(QualityFinding(truth.defect, "truth", 1.0))
                options = (
                    {o.action: o.invalidates for o in decision.options}
                    if isinstance(decision, ClarificationRequest)
                    else {decision.action: decision.invalidates}
                )
                self.assertEqual(dict(truth.acceptable_repairs), options)

    def test_version_ids_carry_no_class_or_difficulty_hints(self) -> None:
        for case in CASES:
            for version in case.versions.values():
                with self.subTest(version=version):
                    self.assertRegex(
                        version,
                        r"^(script|tts|avatar|voice|caption)-h\d\d$|^video:h\d\d$",
                    )

    def test_rejects_inconsistent_ground_truth(self) -> None:
        data = json.loads(BASELINE_DATASET.read_text())
        broken = [
            ("preserve", lambda t: t.update(preserve=[])),
            ("diagnose", lambda t: t.update(human_decision="diagnose")),
        ]
        for name, change in broken:
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                copy = json.loads(json.dumps(data))
                change(copy["cases"][0]["ground_truth"])
                path = Path(directory) / "cases.json"
                path.write_text(json.dumps(copy))
                with self.assertRaises(ValueError):
                    load_baseline_cases(path)


class ArmTests(unittest.TestCase):
    def test_perfect_model_saves_diagnosis_only_on_gate_silent_defects(self) -> None:
        rows = score_run(recorded({}), CASES, policy=DEFAULT_POLICY)
        for case in CASES:
            results = rows[case.id]
            with self.subTest(case=case.id):
                expected = Route.GATE if case.gate_detects else Route.HUMAN
                self.assertIs(results["rules_only"].decision.route, expected)
                for arm in ("single_shot", "layered"):
                    score = results[arm].score
                    self.assertEqual(
                        score.false_passes + score.wrong_repairs + score.false_blocks,
                        0,
                    )
                if case.gate_detects:
                    self.assertEqual(
                        results["layered"].decision, results["rules_only"].decision
                    )

    def test_layered_keeps_gate_authority_against_adversarial_output(self) -> None:
        case = BY_ID["caption-overlap-rerender-adversarial"]
        wrong = oracle_output(case, FailureKind.VISUAL_QUALITY)
        rows = score_run(recorded({case.id: wrong}), CASES, policy=DEFAULT_POLICY)[
            case.id
        ]
        self.assertIs(rows["layered"].decision.route, Route.GATE)
        self.assertEqual(rows["layered"].score.false_passes, 0)
        single = rows["single_shot"].score
        self.assertEqual((single.false_passes, single.wrong_repairs), (1, 1))
        self.assertEqual(single.extra_provider_jobs, 1)
        self.assertEqual(single.unnecessary_invalidations, 1)
        self.assertEqual(single.correction_rounds, 1)
        self.assertEqual(paired_change(single, rows["rules_only"].score), "worse")

    def test_wrong_model_diagnosis_on_gate_gap_is_a_false_pass(self) -> None:
        case = BY_ID["tts-abbreviation-undetected"]
        rows = score_run(
            recorded({case.id: oracle_output(case, FailureKind.SCRIPT_QUALITY)}),
            CASES,
            policy=DEFAULT_POLICY,
        )[case.id]
        layered = rows["layered"].score
        self.assertEqual((layered.false_passes, layered.wrong_repairs), (1, 1))
        self.assertEqual(layered.unnecessary_invalidations, 1)  # the script

    def test_model_cannot_choose_a_creative_branch(self) -> None:
        case = BY_ID["environment-garden-undeclared"]
        output = json.loads(oracle_output(case))
        output.update(
            action="change_avatar", invalidates=["avatar", "captions", "video"]
        )
        rows = score_run(
            recorded({case.id: json.dumps(output)}), CASES, policy=DEFAULT_POLICY
        )
        layered = rows[case.id]["layered"]
        self.assertIs(layered.decision.route, Route.HUMAN)
        self.assertEqual(layered.score.rejected_outputs, 1)
        self.assertEqual(layered.score.unauthorized_actions, 0)
        self.assertEqual(layered.score.false_blocks, 1)

    def test_recorded_provider_error_routes_to_human(self) -> None:
        case = BY_ID["script-steps-out-of-order"]
        rows = score_run(recorded({case.id: None}), CASES, policy=DEFAULT_POLICY)[
            case.id
        ]
        self.assertIs(rows["single_shot"].decision.route, Route.HUMAN)
        self.assertEqual(rows["single_shot"].score.false_blocks, 1)

    def test_ambiguous_hold_without_uncertainty_citation_is_recorded(self) -> None:
        case = BY_ID["visual-single-jump-unsure"]
        output = json.loads(oracle_output(case))
        output["evidence_indices"] = []
        rows = score_run(
            recorded({case.id: json.dumps(output)}), CASES, policy=DEFAULT_POLICY
        )
        score = rows[case.id]["layered"].score
        self.assertEqual(score.uncertainty_omissions, 1)
        self.assertEqual(score.false_passes, 0)


class VerdictTests(unittest.TestCase):
    def test_safety_regression_rejects_regardless_of_time_saved(self) -> None:
        base = [{"human_minutes": 100.0, **dict.fromkeys(SAFETY_METRICS, 0)}]
        arm = [
            {
                "human_minutes": 10.0,
                **dict.fromkeys(SAFETY_METRICS, 0),
                "wrong_repairs": 1,
            }
        ]
        self.assertEqual(verdict(arm, base), "reject_safety_regression")

    def test_adoption_needs_the_reduction_in_every_run(self) -> None:
        zero = dict.fromkeys(SAFETY_METRICS, 0)
        base = [{"human_minutes": 100.0, **zero}] * 2
        both = [{"human_minutes": 85.0, **zero}] * 2
        one = [{"human_minutes": 50.0, **zero}, {"human_minutes": 90.0, **zero}]
        self.assertEqual(verdict(both, base), "adopt")
        self.assertEqual(verdict(one, base), "no_demonstrated_value")

    def test_paired_change_ranks_safety_before_time(self) -> None:
        base = _score()
        self.assertEqual(paired_change(_score(diagnosis_rounds=0), base), "same")
        base = _score(diagnosis_rounds=1)
        self.assertEqual(paired_change(_score(), base), "better")
        self.assertEqual(
            paired_change(_score(false_passes=1, correction_rounds=0), base), "worse"
        )


class RecordingTests(unittest.TestCase):
    def test_records_each_case_once_with_cost_time_and_errors(self) -> None:
        provider = _MeteredOracle(fail={"caption-feel-unsure"})
        ticks = iter(range(100))
        run = record_run("r1", CASES, provider, clock=lambda: float(next(ticks)))
        self.assertEqual(provider.calls, len(CASES))
        call = run["cases"]["caption-feel-unsure"]
        self.assertIsNone(call["raw_output"])
        self.assertEqual(call["error"], "OpenRouter request failed or timed out")
        ok = run["cases"]["script-steps-out-of-order"]
        self.assertEqual((ok["elapsed_seconds"], ok["cost_usd"]), (1.0, 0.0002))
        self.assertEqual(run["model"], "oracle")

    def test_loaded_runs_replay_and_reject_changed_cases(self) -> None:
        run = record_run("r1", CASES, _MeteredOracle(), clock=lambda: 0.0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.json"
            path.write_text(json.dumps({"schema_version": 1, "runs": [run]}))
            (loaded,) = load_runs(path, CASES)
            self.assertEqual(loaded.outputs[CASES[0].id], oracle_output(CASES[0]))
            changed = (replace(CASES[0], feedback="Edited later."), *CASES[1:])
            with self.assertRaisesRegex(ValueError, "differs from the request"):
                load_runs(path, changed)

    def test_report_and_snapshot_gate(self) -> None:
        report = compare_arms(CASES, (recorded({}, "r1"), recorded({}, "r2")))
        self.assertEqual(report["verdicts"]["layered"], "adopt")
        self.assertEqual(report["paired_with_rules_only"]["layered"]["worse"], 0)
        self.assertEqual(
            report["attribution"]["evidence_selection"],
            "not implemented: every arm gets the same evidence",
        )
        self.assertEqual(report["totals"]["rules_only"]["model_calls"], 0)
        layered_calls = 2 * sum(not case.gate_detects for case in CASES)
        self.assertEqual(report["totals"]["layered"]["model_calls"], layered_calls)
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory) / "runs.json"
            run = record_run("r1", CASES, _MeteredOracle(), clock=lambda: 0.0)
            runs.write_text(json.dumps({"schema_version": 1, "runs": [run]}))
            code, out, _ = _run_main("--runs", str(runs))
            self.assertEqual(code, 0)
            expected = Path(directory) / "expected.json"
            expected.write_text(json.dumps(out))
            self.assertEqual(
                _run_main("--runs", str(runs), "--expect", str(expected))[0], 0
            )
            out["verdicts"]["layered"] = "reject_safety_regression"
            expected.write_text(json.dumps(out))
            code, _, errors = _run_main("--runs", str(runs), "--expect", str(expected))
            self.assertEqual(code, 1)
            self.assertIn("verdicts.layered", errors)


class _MeteredOracle:
    identity = IDENTITY

    def __init__(self, fail: set[str] | None = None) -> None:
        self.calls = 0
        self._fail = {
            interpretation_request(BY_ID[i], run_gates(BY_ID[i])) for i in fail or set()
        }
        self._answers = {
            interpretation_request(case, run_gates(case)): oracle_output(case)
            for case in CASES
        }

    def interpret_metered(self, request: InterpretationRequest) -> tuple[str, float]:
        self.calls += 1
        if request in self._fail:
            raise ModelProviderError("OpenRouter request failed or timed out")
        return self._answers[request], 0.0002


def _score(**changes: int) -> CaseScore:
    fields = dict.fromkeys(CaseScore.__dataclass_fields__, 0)
    return CaseScore(**{**fields, **changes})


def _run_main(*arguments: str) -> tuple[int, dict[str, Any], str]:
    output, errors = io.StringIO(), io.StringIO()
    code = 0
    with (
        patch("sys.argv", ["baseline", *arguments]),
        redirect_stdout(output),
        redirect_stderr(errors),
    ):
        try:
            main()
        except SystemExit as exit_:
            assert isinstance(exit_.code, int)
            code = exit_.code
    return code, json.loads(output.getvalue()), errors.getvalue()
