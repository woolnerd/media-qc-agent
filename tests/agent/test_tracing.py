"""Model-turn spans: version identity, validation outcome, and content privacy."""

import json
import unittest
from typing import Any
from unittest.mock import Mock

from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from media_qc_agent.agent.contracts import FakeModelProvider, InterpretationRequest
from media_qc_agent.agent.interpretation import GROUNDED_SCOPE_POLICY
from media_qc_agent.agent.openrouter import ModelProviderError
from media_qc_agent.agent.tracing import TurnOutcome, interpret_traced
from media_qc_agent.cli.evaluate import EvaluationCase, fixture_provider, load_cases
from media_qc_agent.domain.ids import reference


def _case(case_id: str) -> EvaluationCase:
    return next(case for case in load_cases() if case.id == case_id)


def _attributes(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


class InterpretationTracingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.addCleanup(provider.shutdown)
        self.tracer = provider.get_tracer("test")

    def spans(self) -> dict[str, ReadableSpan]:
        return {span.name: span for span in self.exporter.get_finished_spans()}

    def test_accepted_turn_nests_diagnosis_and_planning_with_versions(self) -> None:
        case = _case("visual-quality-clear")
        turn = interpret_traced(
            fixture_provider((case,)), case.request, tracer=self.tracer, run_id="run-1"
        )
        self.assertIs(turn.outcome, TurnOutcome.ACCEPTED)
        self.assertEqual(turn.raw_output, case.fake_response)
        assert turn.interpretation is not None and turn.interpretation.finding
        spans = self.spans()
        self.assertEqual(
            set(spans), {"agent.interpret", "agent.diagnose", "agent.plan"}
        )
        root = spans["agent.interpret"]
        for child in (spans["agent.diagnose"], spans["agent.plan"]):
            self.assertEqual(
                child.parent and child.parent.span_id, root.context.span_id
            )
        self.assertEqual(turn.trace_id, f"{root.context.trace_id:032x}")
        self.assertEqual(
            _attributes(root),
            {
                "gen_ai.operation.name": "interpret",
                "gen_ai.provider.name": "fake",
                "gen_ai.request.model": "fixture",
                "agent.prompt.version": "fixture",
                "agent.policy.version": "confidence-v1",
                "agent.artifact_refs": tuple(
                    reference(v) for v in case.request.artifact_version_ids
                ),
                "workflow.run_ref": reference("run-1"),
                "agent.outcome": "accepted",
            },
        )
        self.assertEqual(
            _attributes(spans["agent.plan"]),
            {
                "agent.outcome": "accepted",
                "agent.failure_kind": "visual_quality",
                "agent.repair_actions": ("regenerate_video",),
                "agent.clarification": "none",
                "agent.cited_evidence": len(case.expected.evidence_indices),
            },
        )

    def test_spans_never_carry_feedback_evidence_or_raw_ids(self) -> None:
        for case in load_cases():
            interpret_traced(
                fixture_provider((case,)), case.request, tracer=self.tracer
            )
            exported = json.dumps(
                [_attributes(s) for s in self.exporter.get_finished_spans()],
                default=list,
            )
            with self.subTest(case=case.id):
                self.assertNotIn(case.request.feedback, exported)
                for item in case.request.evidence:
                    self.assertNotIn(item.statement, exported)
                for version in case.request.artifact_version_ids:
                    self.assertNotIn(f'"{version}"', exported)
            self.exporter.clear()

    def test_ambiguous_case_abstains_without_error_or_run_link(self) -> None:
        case = _case("visual-quality-ambiguous")
        turn = interpret_traced(
            fixture_provider((case,)), case.request, tracer=self.tracer
        )
        self.assertIs(turn.outcome, TurnOutcome.ABSTAINED)
        spans = self.spans()
        self.assertNotIn("workflow.run_ref", _attributes(spans["agent.interpret"]))
        plan = _attributes(spans["agent.plan"])
        self.assertEqual(plan["agent.clarification"], "diagnostic")
        self.assertEqual(plan["agent.repair_actions"], ())
        for span in spans.values():
            self.assertIsNot(span.status.status_code, StatusCode.ERROR)

    def test_creative_choice_records_every_offered_branch(self) -> None:
        case = _case("environment-mismatch-clear")
        turn = interpret_traced(
            fixture_provider((case,)), case.request, tracer=self.tracer
        )
        self.assertIs(turn.outcome, TurnOutcome.ACCEPTED)
        plan = _attributes(self.spans()["agent.plan"])
        self.assertEqual(plan["agent.clarification"], "creative")
        self.assertEqual(
            plan["agent.repair_actions"], ("change_avatar", "revise_script")
        )

    def test_rejected_output_keeps_raw_output_and_marks_only_planning(self) -> None:
        case = _case("caption-format-adversarial")
        malicious = json.loads(case.fake_response)
        malicious.update(action="regenerate_video", invalidates=["video", "captions"])
        raw = json.dumps(malicious)
        turn = interpret_traced(
            FakeModelProvider({case.request: raw}), case.request, tracer=self.tracer
        )
        self.assertIs(turn.outcome, TurnOutcome.REJECTED)
        self.assertIsNone(turn.interpretation)
        self.assertEqual(turn.raw_output, raw)
        spans = self.spans()
        self.assertIs(spans["agent.diagnose"].status.status_code, StatusCode.UNSET)
        plan = spans["agent.plan"]
        self.assertIs(plan.status.status_code, StatusCode.ERROR)
        self.assertEqual(plan.status.description, None)
        self.assertEqual(_attributes(plan)["error.type"], "validation_rejected")
        self.assertEqual(plan.events, ())

    def test_provider_error_skips_planning(self) -> None:
        request = InterpretationRequest("Video jumps", ("video-1",))
        provider = Mock(identity=FakeModelProvider({}).identity)
        provider.interpret.side_effect = ModelProviderError("secret body")
        turn = interpret_traced(provider, request, tracer=self.tracer)
        self.assertIs(turn.outcome, TurnOutcome.PROVIDER_ERROR)
        self.assertIsNone(turn.raw_output)
        spans = self.spans()
        self.assertEqual(set(spans), {"agent.interpret", "agent.diagnose"})
        diagnose = spans["agent.diagnose"]
        self.assertEqual(_attributes(diagnose)["error.type"], "provider_error")
        self.assertNotIn("secret", str(diagnose.events) + str(diagnose.status))

    def test_tracer_failure_does_not_change_the_interpretation(self) -> None:
        case = _case("tts-input-compatibility-clear")
        broken = Mock()
        broken.start_as_current_span.side_effect = RuntimeError("exporter down")
        turn = interpret_traced(fixture_provider((case,)), case.request, tracer=broken)
        self.assertIs(turn.outcome, TurnOutcome.ACCEPTED)
        self.assertIsNone(turn.trace_id)

    def test_default_tracer_is_a_no_op_without_a_configured_sdk(self) -> None:
        case = _case("script-quality-clear")
        turn = interpret_traced(fixture_provider((case,)), case.request)
        self.assertIs(turn.outcome, TurnOutcome.ACCEPTED)
        self.assertIsNone(turn.trace_id)

    def test_policy_version_is_traced_and_ungrounded_output_abstains(self) -> None:
        case = _case("tts-input-compatibility-adversarial")
        wrong = json.loads(case.fake_response)
        wrong.update(
            kind="caption_format", action="repair_captions", invalidates=["captions"]
        )
        turn = interpret_traced(
            FakeModelProvider({case.request: json.dumps(wrong)}),
            case.request,
            tracer=self.tracer,
            policy=GROUNDED_SCOPE_POLICY,
        )
        self.assertIs(turn.outcome, TurnOutcome.ABSTAINED)
        self.assertEqual(turn.policy_version, "grounded-scope-v2")
        spans = self.spans()
        self.assertEqual(
            _attributes(spans["agent.interpret"])["agent.policy.version"],
            "grounded-scope-v2",
        )
        self.assertEqual(_attributes(spans["agent.plan"])["agent.outcome"], "abstained")
