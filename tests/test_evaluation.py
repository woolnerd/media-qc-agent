import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from media_qc_agent.domain import ArtifactKind, FailureKind, RepairAction, RepairPlan
from media_qc_agent.evaluation import (
    DEFAULT_DATASET,
    evaluate_case,
    fixture_provider,
    load_cases,
    score_interpretation,
)
from media_qc_agent.interpretation import interpret_feedback
from media_qc_agent.model import FakeModelProvider


class EvaluationDatasetTests(unittest.TestCase):
    def test_dataset_covers_each_failure_class_and_difficulty(self) -> None:
        cases = load_cases()
        self.assertEqual(len(cases), 15)
        for kind in FailureKind:
            self.assertEqual(
                {case.category for case in cases if case.failure_class is kind},
                {"clear", "ambiguous", "adversarial"},
            )
        for case in cases:
            with self.subTest(case=case.id):
                self.assertEqual(len(case.request.artifact_version_ids), 6)
                self.assertTrue(case.request.evidence)
                self.assertEqual(
                    case.expected.allowed_actions | case.expected.forbidden_actions,
                    frozenset(RepairAction),
                )
                self.assertFalse(
                    case.expected.allowed_actions & case.expected.forbidden_actions
                )
                if case.category == "ambiguous":
                    self.assertIsNone(case.expected.kind)
                    self.assertEqual(case.expected.clarification, "diagnostic")
                    self.assertFalse(case.expected.allowed_actions)

    def test_saved_safe_responses_replay_without_network(self) -> None:
        cases = load_cases()
        provider = fixture_provider(cases)
        for case in cases:
            with self.subTest(case=case.id):
                self.assertTrue(evaluate_case(case, provider).passed)

    def test_bad_response_is_counted_as_failure_without_execution(self) -> None:
        case = next(
            case for case in load_cases() if case.id == "caption-format-adversarial"
        )
        malicious = json.loads(case.fake_response)
        malicious.update(action="regenerate_video", invalidates=["video", "captions"])
        result = evaluate_case(
            case, FakeModelProvider({case.request: json.dumps(malicious)})
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.failures, ("provider_or_validation_error",))

    def test_confident_wrong_diagnosis_does_not_pass_validation_alone(self) -> None:
        case = next(
            case for case in load_cases() if case.id == "visual-quality-adversarial"
        )
        wrong = json.loads(case.fake_response)
        wrong.update(
            kind="caption_format", action="repair_captions", invalidates=["captions"]
        )
        result = evaluate_case(
            case, FakeModelProvider({case.request: json.dumps(wrong)})
        )
        self.assertFalse(result.passed)
        self.assertIn("classification", result.failures)
        self.assertIn("repair_scope", result.failures)

    def test_scorer_separately_detects_evidence_clarification_and_scope_errors(
        self,
    ) -> None:
        case = next(case for case in load_cases() if case.id == "visual-quality-clear")
        good = interpret_feedback(fixture_provider((case,)), case.request)
        assert isinstance(good.decision, RepairPlan)
        wrong = replace(
            good,
            evidence=(),
            clarification="Which artifact?",
            decision=replace(
                good.decision, invalidates=frozenset({ArtifactKind.VIDEO})
            ),
        )
        self.assertEqual(
            score_interpretation(case, wrong).failures,
            ("evidence", "clarification", "repair_scope"),
        )

    def test_dataset_rejects_duplicate_ids_missing_inputs_and_unbound_evidence(
        self,
    ) -> None:
        data: dict[str, Any] = json.loads(DEFAULT_DATASET.read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            for mutation in (
                "duplicate",
                "missing_input",
                "foreign_evidence",
                "missing_scope",
            ):
                sample = json.loads(json.dumps(data))
                if mutation == "duplicate":
                    sample["cases"].append(sample["cases"][0])
                elif mutation == "missing_input":
                    del sample["cases"][0]["input_versions"]["voice"]
                elif mutation == "foreign_evidence":
                    sample["cases"][0]["evidence"][0]["artifact_version_id"] = (
                        "script-foreign"
                    )
                else:
                    sample["cases"][0]["expected"]["invalidations_by_action"] = {}
                path.write_text(json.dumps(sample))
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    load_cases(path)
