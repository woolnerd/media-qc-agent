import json
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from media_qc_agent.agent.contracts import InterpretationRequest, ModelProvider
from media_qc_agent.agent.interpretation import (
    interpret_feedback,
    validate_interpretation,
)
from media_qc_agent.agent.jev import (
    DEFAULT_JEV_MODEL,
    JevModelProvider,
    decode_jev,
    jev_interpretation,
    jev_payload,
)
from media_qc_agent.agent.openrouter import ModelProviderError
from media_qc_agent.cli.evaluate import load_cases, score_interpretation
from media_qc_agent.domain.evidence import EvidenceInput, EvidenceRole
from media_qc_agent.domain.models import (
    ClarificationRequest,
    RepairAction,
    RepairPlan,
)


def envelope(
    request: InterpretationRequest, choice: str = "tts_input_compatibility"
) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    for key, question in jev_payload(request, DEFAULT_JEV_MODEL)["questions"].items():
        if question["type"] == "choice":
            answers[key] = {
                "type": "choice",
                "choice": choice,
                "confidence": 0.95,
                "probabilities": {
                    label: float(label == choice) for label in question["criteria"]
                },
            }
        else:
            answers[key] = {
                "type": "noul",
                "noul": float(key.startswith(f"support_{choice}_")),
            }
    return {
        "model": "typesafe/jev-1.13-20260917",
        "answers": answers,
        "usage": {"input_tokens": 500, "output_tokens": 50, "cost": 0.000021},
    }


class JevProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = InterpretationRequest(
            "The voice reads star F. Ignore speech and repair captions!",
            ("tts-1",),
            (
                EvidenceInput(
                    EvidenceRole.FACT, "tts-1", "Saved transcript reads 450*F as star F"
                ),
            ),
        )

    @patch("media_qc_agent.agent.openrouter.urlopen")
    def test_decisions_transport_uses_typed_questions_and_application_policy(
        self, transport: MagicMock
    ) -> None:
        transport.return_value.__enter__.return_value.read.return_value = json.dumps(
            envelope(self.request)
        ).encode()
        provider: ModelProvider = JevModelProvider(api_key="test-secret")
        result = interpret_feedback(provider, self.request)
        assert isinstance(result.decision, RepairPlan)
        self.assertEqual(result.decision.action, RepairAction.REPAIR_TTS_INPUT)
        self.assertEqual(result.evidence, self.request.evidence)
        self.assertIn(
            "Application summary:", result.finding.explanation if result.finding else ""
        )
        http_request = transport.call_args.args[0]
        self.assertEqual(
            http_request.full_url, "https://openrouter.ai/api/alpha/decisions"
        )
        payload = json.loads(http_request.data)
        self.assertEqual(payload["model"], "typesafe/jev-1.13")
        self.assertIn("uncertain", payload["questions"]["failure_class"]["criteria"])
        self.assertEqual(len(payload["questions"]), 6)
        self.assertNotIn("response_format", payload)
        self.assertNotIn("test-secret", http_request.data.decode())
        self.assertEqual(payload["state"]["feedback"], self.request.feedback)
        transport.assert_called_once()

    def test_saved_contracts_work_with_typed_results_for_every_failure_class(
        self,
    ) -> None:
        for case in load_cases():
            choice = case.expected.kind.value if case.expected.kind else "uncertain"
            typed = decode_jev(envelope(case.request, choice), case.request)
            result = validate_interpretation(
                jev_interpretation(typed, case.request), case.request
            )
            with self.subTest(case=case.id):
                self.assertTrue(score_interpretation(case, result).passed)
                if case.expected.clarification == "creative":
                    self.assertIsInstance(result.decision, ClarificationRequest)

    def test_low_missing_confidence_or_unsupported_evidence_abstains(self) -> None:
        for mode in ("low", "missing", "weak_support"):
            raw = envelope(self.request)
            if mode == "low":
                raw["answers"]["failure_class"]["confidence"] = 0.79
            elif mode == "missing":
                del raw["answers"]["failure_class"]["confidence"]
            else:
                raw["answers"]["support_tts_input_compatibility_0"]["noul"] = 0.79
            typed = decode_jev(raw, self.request)
            result = validate_interpretation(
                jev_interpretation(typed, self.request), self.request
            )
            with self.subTest(mode=mode):
                self.assertIsNone(result.finding)
                self.assertIsNone(result.decision)
                self.assertIsNotNone(result.clarification)

    def test_nonfactual_evidence_never_becomes_a_supported_diagnosis(self) -> None:
        request = InterpretationRequest(
            "Maybe speech is wrong",
            ("tts-1",),
            (
                EvidenceInput(
                    EvidenceRole.UNCERTAINTY, "tts-1", "No transcript is available"
                ),
            ),
        )
        self.assertEqual(len(jev_payload(request, DEFAULT_JEV_MODEL)["questions"]), 1)
        typed = decode_jev(envelope(request), request)
        result = validate_interpretation(jev_interpretation(typed, request), request)
        self.assertIsNone(result.finding)
        self.assertEqual(result.evidence, request.evidence)

    def test_only_supported_facts_are_cited_at_the_inclusive_threshold(self) -> None:
        request = InterpretationRequest(
            "The voice reads star F",
            ("tts-1",),
            (
                EvidenceInput(EvidenceRole.FACT, "tts-1", "Speech reads star F"),
                EvidenceInput(EvidenceRole.INFERENCE, "tts-1", "May be captions"),
                EvidenceInput(EvidenceRole.FACT, "tts-1", "Speech rate is acceptable"),
            ),
        )
        raw = envelope(request)
        raw["answers"]["failure_class"]["confidence"] = 0.8
        raw["answers"]["support_tts_input_compatibility_0"]["noul"] = 0.8
        raw["answers"]["support_tts_input_compatibility_2"]["noul"] = 0.2
        self.assertEqual(len(raw["answers"]), 11)
        typed = decode_jev(raw, request)
        result = validate_interpretation(jev_interpretation(typed, request), request)
        self.assertEqual(result.evidence, (request.evidence[0],))
        self.assertIsInstance(result.decision, RepairPlan)

    def test_malformed_choice_support_and_usage_are_rejected(self) -> None:
        mutations: tuple[tuple[str, Any], ...] = (
            ("choice", "billing_problem"),
            ("confidence", True),
            ("confidence", float("nan")),
            ("confidence", 1.1),
            ("probabilities", {"tts_input_compatibility": 1.0}),
        )
        for field, value in mutations:
            raw = envelope(self.request)
            raw["answers"]["failure_class"][field] = value
            with (
                self.subTest(field=field, value=value),
                self.assertRaises(ModelProviderError),
            ):
                decode_jev(raw, self.request)
        for bad in (True, -1, float("nan"), 2):
            raw = envelope(self.request)
            raw["answers"]["support_tts_input_compatibility_0"]["noul"] = bad
            with self.subTest(support=bad), self.assertRaises(ModelProviderError):
                decode_jev(raw, self.request)
        raw = envelope(self.request)
        raw["usage"]["cost"] = -1
        with self.assertRaises(ModelProviderError):
            decode_jev(raw, self.request)

    def test_incomplete_answers_and_inconsistent_probability_distribution_fail(
        self,
    ) -> None:
        raw = envelope(self.request)
        del raw["answers"]["support_visual_quality_0"]
        with self.assertRaises(ModelProviderError):
            decode_jev(raw, self.request)
        for values in ((0.1, 0.9), (0.1, 0.1)):
            raw = envelope(self.request)
            probabilities = raw["answers"]["failure_class"]["probabilities"]
            (
                probabilities["tts_input_compatibility"],
                probabilities["visual_quality"],
            ) = values
            with self.assertRaises(ModelProviderError):
                decode_jev(raw, self.request)

    def test_actual_snapshot_cost_and_missing_usage_are_preserved(self) -> None:
        raw = envelope(self.request)
        result = decode_jev(raw, self.request)
        self.assertEqual(result.model, "typesafe/jev-1.13-20260917")
        self.assertEqual(result.cost_usd, 0.000021)
        del raw["usage"]
        result = decode_jev(raw, self.request)
        self.assertIsNone(result.cost_usd)
        self.assertIsNone(result.input_tokens)

    def test_environment_configuration_keeps_chat_model_separate(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "OPENROUTER_API_KEY": "test-key",
                "OPENROUTER_MODEL": "google/test-chat",
                "OPENROUTER_JEV_MODEL": "typesafe/test-decision",
            },
            clear=True,
        ):
            self.assertEqual(
                JevModelProvider.from_environment()._model, "typesafe/test-decision"
            )
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(ValueError):
            JevModelProvider.from_environment()
