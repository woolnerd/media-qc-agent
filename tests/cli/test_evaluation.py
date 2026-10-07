import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from media_qc_agent.agent.contracts import FakeModelProvider
from media_qc_agent.agent.interpretation import interpret_feedback
from media_qc_agent.cli.evaluate import (
    DEFAULT_DATASET,
    evaluate_case,
    fixture_provider,
    load_cases,
    main,
    mask_version_labels,
    score_interpretation,
    trace_record,
)
from media_qc_agent.domain.models import (
    ArtifactKind,
    FailureKind,
    RepairAction,
    RepairPlan,
)


class EvaluationDatasetTests(unittest.TestCase):
    def test_cli_selects_live_provider_and_keeps_offline_runs_fake(self) -> None:
        cases = tuple(mask_version_labels(case) for case in load_cases())
        fake = fixture_provider(cases)
        for name, live in (("chat", True), ("jev", True), ("jev", False)):
            arguments = ["evaluation", "--provider", name, "--mask-version-labels"]
            if live:
                arguments.append("--live")
            output = io.StringIO()
            with (
                self.subTest(provider=name, live=live),
                patch("sys.argv", arguments),
                patch(
                    "media_qc_agent.cli.evaluate.OpenRouterModelProvider.from_environment",
                    return_value=fake,
                ) as chat,
                patch(
                    "media_qc_agent.cli.evaluate.JevModelProvider.from_environment",
                    return_value=fake,
                ) as jev,
                redirect_stdout(output),
                self.assertRaises(SystemExit) as exit_context,
            ):
                main()
            self.assertEqual(exit_context.exception.code, 0)
            self.assertEqual(chat.call_count, int(live and name == "chat"))
            self.assertEqual(jev.call_count, int(live and name == "jev"))
            report = json.loads(output.getvalue())
            self.assertEqual(report["provider"], name if live else "fake")
            self.assertTrue(report["version_labels_masked"])
            self.assertEqual(report["passed"], 15)

    def test_masked_versions_remove_labels_and_preserve_evidence_links(self) -> None:
        cases = load_cases()
        for original in cases:
            masked = mask_version_labels(original)
            with self.subTest(case=original.id):
                self.assertEqual(masked.id, original.id)
                self.assertEqual(masked.expected, original.expected)
                self.assertEqual(masked.request.feedback, original.request.feedback)
                for old, new in zip(
                    original.request.evidence, masked.request.evidence, strict=True
                ):
                    self.assertNotEqual(
                        old.artifact_version_id, new.artifact_version_id
                    )
                    self.assertIn(
                        new.artifact_version_id, masked.request.artifact_version_ids
                    )
                    self.assertEqual(
                        (old.role, old.statement, old.observed, old.limit),
                        (new.role, new.statement, new.observed, new.limit),
                    )
                self.assertTrue(
                    evaluate_case(masked, fixture_provider((masked,))).passed
                )
        self.assertEqual(
            len(
                {
                    mask_version_labels(case).request.artifact_version_ids
                    for case in cases
                }
            ),
            1,
        )

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


class EvaluationTraceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.addCleanup(provider.shutdown)
        self.tracer = provider.get_tracer("test")
        self.cases = {case.id: case for case in load_cases()}

    def test_case_span_wraps_the_turn_and_records_the_score(self) -> None:
        case = self.cases["visual-quality-adversarial"]
        wrong = json.loads(case.fake_response)
        wrong.update(
            kind="caption_format", action="repair_captions", invalidates=["captions"]
        )
        provider = FakeModelProvider({case.request: json.dumps(wrong)})
        result = evaluate_case(case, provider, tracer=self.tracer)
        spans = {span.name: span for span in self.exporter.get_finished_spans()}
        root = spans["eval.case"]
        self.assertEqual(
            spans["agent.interpret"].parent and spans["agent.interpret"].parent.span_id,
            root.context.span_id,
        )
        self.assertEqual(
            dict(root.attributes or {}),
            {
                "eval.case_id": case.id,
                "eval.category": "adversarial",
                "eval.passed": False,
                "eval.failures": result.failures,
            },
        )
        assert result.turn is not None
        self.assertEqual(result.turn.trace_id, f"{root.context.trace_id:032x}")

    def test_record_keeps_synthetic_inputs_output_version_and_score(self) -> None:
        case = self.cases["environment-mismatch-clear"]
        result = evaluate_case(case, fixture_provider((case,)), tracer=self.tracer)
        record = json.loads(json.dumps(trace_record(case, result)))
        self.assertEqual(
            record["model"],
            {"provider": "fake", "model": "fixture", "prompt_version": "fixture"},
        )
        self.assertEqual(record["case_id"], case.id)
        self.assertEqual(record["category"], "clear")
        self.assertEqual(
            record["artifact_version_ids"], list(case.request.artifact_version_ids)
        )
        self.assertEqual(record["input"]["feedback"], case.request.feedback)
        self.assertEqual(len(record["input"]["evidence"]), len(case.request.evidence))
        self.assertEqual(record["raw_output"], case.fake_response)
        self.assertEqual(record["outcome"], "accepted")
        self.assertEqual(
            record["interpretation"],
            {
                "kind": "environment_mismatch",
                "clarification": "creative",
                "cited_evidence_indices": list(case.expected.evidence_indices),
                "repair_scopes": {
                    "change_avatar": ["avatar", "captions", "video"],
                    "revise_script": ["captions", "script", "tts_input", "video"],
                },
            },
        )
        self.assertEqual(record["policy_version"], "grounded-scope-v2")
        self.assertEqual(record["score"], {"passed": True, "failures": []})
        self.assertRegex(record["trace_id"], r"\A[0-9a-f]{32}\Z")

    def test_rejected_output_is_recorded_without_an_interpretation(self) -> None:
        case = self.cases["caption-format-adversarial"]
        malicious = json.loads(case.fake_response)
        malicious.update(action="regenerate_video", invalidates=["video", "captions"])
        raw = json.dumps(malicious)
        result = evaluate_case(case, FakeModelProvider({case.request: raw}))
        record = trace_record(case, result)
        self.assertEqual(record["outcome"], "rejected")
        self.assertIsNone(record["interpretation"])
        self.assertEqual(record["raw_output"], raw)
        self.assertIsNone(record["trace_id"])
        self.assertEqual(
            record["score"],
            {"passed": False, "failures": ["provider_or_validation_error"]},
        )

    def test_cli_writes_one_trace_line_per_case_and_reports_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "traces.jsonl"
            output = io.StringIO()
            with (
                patch("sys.argv", ["evaluation", "--traces", str(path)]),
                redirect_stdout(output),
                self.assertRaises(SystemExit) as exit_context,
            ):
                main()
            self.assertEqual(exit_context.exception.code, 0)
            lines = [json.loads(line) for line in path.read_text().splitlines()]
        report = json.loads(output.getvalue())
        self.assertEqual(report["model"]["prompt_version"], "fixture")
        self.assertEqual(report["policy_version"], "grounded-scope-v2")
        self.assertEqual(report["traces"], str(path))
        self.assertEqual([line["case_id"] for line in lines], list(self.cases))
        self.assertTrue(all(line["score"]["passed"] for line in lines))

    def test_cli_applies_the_selected_policy_version(self) -> None:
        output = io.StringIO()
        with (
            patch("sys.argv", ["evaluation", "--policy", "confidence-v1"]),
            redirect_stdout(output),
            self.assertRaises(SystemExit) as exit_context,
        ):
            main()
        self.assertEqual(exit_context.exception.code, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["policy_version"], "confidence-v1")
        self.assertEqual(report["passed"], 15)
