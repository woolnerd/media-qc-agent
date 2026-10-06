import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

from media_qc_agent.agent.contracts import FakeModelProvider
from media_qc_agent.agent.interpretation import (
    CONFIDENCE_POLICY,
    GROUNDED_SCOPE_POLICY,
    POLICIES,
)
from media_qc_agent.cli.compare import (
    DEFAULT_EXPECTED,
    DEFAULT_RECORDINGS,
    EVALS,
    compare,
    differences,
    error_type,
    fixture_run,
    load_recordings,
    main,
    replay,
)
from media_qc_agent.cli.evaluate import (
    DEFAULT_DATASET,
    CaseResult,
    EvaluationCase,
    evaluate_case,
    load_cases,
)

TTS_ADVERSARIAL = "tts-input-compatibility-adversarial"


def _source_case(run: dict[str, Any], case_id: str) -> dict[str, Any]:
    path, _, provider = run["source"].partition("#")
    entry = next(
        item
        for item in json.loads((EVALS / path).read_text())["results"]
        if item["id"] == case_id
    )
    return entry["providers"][provider] if provider else entry


def _run_main(*arguments: str) -> tuple[int, dict[str, Any], str]:
    output, errors = io.StringIO(), io.StringIO()
    try:
        with (
            patch("sys.argv", ["compare", *arguments]),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            main()
    except SystemExit as exit_:
        assert isinstance(exit_.code, int)
        return exit_.code, json.loads(output.getvalue()), errors.getvalue()
    raise AssertionError("compare must exit with a status code")


class RecordedOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cases = load_cases()
        self.runs = load_recordings(DEFAULT_RECORDINGS, self.cases)

    def test_recordings_match_their_saved_live_result_sources(self) -> None:
        data = json.loads(DEFAULT_RECORDINGS.read_text())
        for run in data["runs"]:
            for case_id, recorded in run["cases"].items():
                with self.subTest(run=run["id"], case=case_id):
                    source = _source_case(run, case_id)
                    self.assertEqual(recorded["output"], source["output"])
                    self.assertEqual(recorded["failures"], source["failures"])

    def test_original_policy_replay_reproduces_recorded_scores(self) -> None:
        for run in self.runs:
            results = replay(run, self.cases, CONFIDENCE_POLICY)
            for case, result in zip(self.cases, results, strict=True):
                with self.subTest(run=run.id, case=case.id):
                    self.assertEqual(result.failures, run.recorded_failures[case.id])
                    assert result.turn is not None
                    self.assertEqual(result.turn.identity, run.identity)

    def test_recordings_reject_incomplete_or_duplicate_runs(self) -> None:
        data: dict[str, Any] = json.loads(DEFAULT_RECORDINGS.read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recordings.json"
            for mutation in ("missing_case", "duplicate", "fixture_id", "extra"):
                sample = json.loads(json.dumps(data))
                if mutation == "missing_case":
                    del sample["runs"][0]["cases"][TTS_ADVERSARIAL]
                elif mutation == "duplicate":
                    sample["runs"].append(sample["runs"][0])
                elif mutation == "fixture_id":
                    sample["runs"][0]["id"] = "fixture"
                else:
                    sample["runs"][0]["prompt"] = "unrecorded"
                path.write_text(json.dumps(sample))
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    load_recordings(path, self.cases)


class ErrorTypeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cases = {case.id: case for case in load_cases()}

    def result_for(
        self, case_id: str, **changes: Any
    ) -> tuple[EvaluationCase, CaseResult]:
        case = self.cases[case_id]
        output = json.loads(case.fake_response)
        output.update(changes)
        provider = FakeModelProvider({case.request: json.dumps(output)})
        return case, evaluate_case(case, provider)

    def test_wrong_output_with_repair_authority_is_a_false_pass(self) -> None:
        case, result = self.result_for(
            TTS_ADVERSARIAL,
            kind="caption_format",
            action="repair_captions",
            invalidates=["captions"],
        )
        self.assertEqual(result.failures, ("classification", "repair_scope"))
        self.assertEqual(error_type(case, result), "false_pass")

    def test_confident_diagnosis_of_an_ambiguous_case_cannot_gain_authority(
        self,
    ) -> None:
        # Ambiguous cases supply no facts, so no policy can grant authority.
        case, result = self.result_for(
            "visual-quality-ambiguous",
            kind="visual_quality",
            confidence=0.95,
            action="regenerate_video",
            invalidates=["captions", "video"],
        )
        assert result.turn is not None and result.turn.interpretation is not None
        self.assertIsNone(result.turn.interpretation.finding)
        self.assertTrue(result.passed)
        self.assertIsNone(error_type(case, result))

    def test_held_or_rejected_repairable_case_is_a_false_block(self) -> None:
        held: dict[str, Any] = {"kind": None, "action": None, "invalidates": []}
        rejected: dict[str, Any] = {
            "action": "regenerate_video",
            "invalidates": ["video", "captions"],
        }
        for changes in (held, rejected):
            case, result = self.result_for(TTS_ADVERSARIAL, **changes)
            with self.subTest(changes=changes):
                self.assertEqual(error_type(case, result), "false_block")

    def test_correct_hold_with_wrong_citations_is_another_failure(self) -> None:
        case, result = self.result_for("visual-quality-ambiguous", evidence_indices=[])
        self.assertEqual(result.failures, ("evidence",))
        self.assertEqual(error_type(case, result), "other_failure")
        case, result = self.result_for("visual-quality-clear")
        self.assertIsNone(error_type(case, result))


class PolicyComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cases = load_cases()
        self.runs = (
            fixture_run(self.cases),
            *load_recordings(DEFAULT_RECORDINGS, self.cases),
        )
        self.report = compare(self.cases, self.runs, tuple(POLICIES.values()))

    def test_grounded_scope_trades_recorded_false_passes_for_false_blocks(
        self,
    ) -> None:
        totals = self.report["totals"]
        self.assertEqual(
            totals[CONFIDENCE_POLICY.version],
            {
                "passed": 71,
                "total": 75,
                "false_passes": 2,
                "false_blocks": 1,
                "other_failures": 1,
            },
        )
        self.assertEqual(
            totals[GROUNDED_SCOPE_POLICY.version],
            {
                "passed": 71,
                "total": 75,
                "false_passes": 0,
                "false_blocks": 3,
                "other_failures": 1,
            },
        )
        for run_id in ("gemini-unmasked-2026-09-29", "gemini-masked-2026-09-29"):
            by_policy = self.report["runs"][run_id]["by_policy"]
            with self.subTest(run=run_id):
                self.assertEqual(
                    by_policy[CONFIDENCE_POLICY.version]["errors"][TTS_ADVERSARIAL],
                    {
                        "type": "false_pass",
                        "failures": ["classification", "repair_scope"],
                    },
                )
                self.assertEqual(
                    by_policy[GROUNDED_SCOPE_POLICY.version]["errors"][TTS_ADVERSARIAL][
                        "type"
                    ],
                    "false_block",
                )

    def test_only_the_ungrounded_output_changes_between_policies(self) -> None:
        changed = [
            (run_id, case_id)
            for run_id, run in self.report["runs"].items()
            for case_id in set(run["by_policy"]["confidence-v1"]["errors"])
            | set(run["by_policy"]["grounded-scope-v2"]["errors"])
            if run["by_policy"]["confidence-v1"]["errors"].get(case_id)
            != run["by_policy"]["grounded-scope-v2"]["errors"].get(case_id)
        ]
        self.assertEqual(
            sorted(changed),
            [
                ("gemini-masked-2026-09-29", TTS_ADVERSARIAL),
                ("gemini-unmasked-2026-09-29", TTS_ADVERSARIAL),
            ],
        )

    def test_saved_comparison_matches_the_current_replay(self) -> None:
        self.assertEqual(
            differences(json.loads(DEFAULT_EXPECTED.read_text()), self.report), []
        )

    def test_differences_name_each_changed_path(self) -> None:
        self.assertEqual(
            differences({"a": {"b": 1, "c": 2}}, {"a": {"b": 1, "d": 2}}),
            ['a.c: expected 2, got "<missing>"', 'a.d: expected "<missing>", got 2'],
        )


class CompareCliTests(unittest.TestCase):
    def test_matching_snapshot_exits_zero(self) -> None:
        code, report, errors = _run_main("--expect", str(DEFAULT_EXPECTED))
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        self.assertEqual(report["default_policy"], "confidence-v1")

    def test_regressed_snapshot_exits_nonzero_and_names_the_change(self) -> None:
        expected = json.loads(DEFAULT_EXPECTED.read_text())
        expected["totals"]["grounded-scope-v2"]["false_passes"] = 1
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "expected.json"
            path.write_text(json.dumps(expected))
            code, _, errors = _run_main("--expect", str(path))
        self.assertEqual(code, 1)
        self.assertIn(
            "totals.grounded-scope-v2.false_passes: expected 1, got 0", errors
        )

    def test_failing_fixture_exits_nonzero_without_a_snapshot(self) -> None:
        data = json.loads(DEFAULT_DATASET.read_text())
        data["cases"][0]["fake_response"]["action"] = "regenerate_video"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            path.write_text(json.dumps(data))
            code, _, errors = _run_main("--dataset", str(path))
        self.assertEqual(code, 1)
        self.assertIn("fixture fails under confidence-v1", errors)
        self.assertIn("fixture fails under grounded-scope-v2", errors)
