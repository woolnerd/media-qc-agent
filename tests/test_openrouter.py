import json
import unittest
from email.message import Message
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from media_qc_agent.model import InterpretationRequest, ModelProvider
from media_qc_agent.openrouter import (
    DEFAULT_MODEL,
    ModelProviderError,
    OpenRouterModelProvider,
)


class OpenRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider: ModelProvider = OpenRouterModelProvider(api_key="test-secret")
        self.request = InterpretationRequest("Video jumps", ("video-1",))

    @patch("media_qc_agent.openrouter.urlopen")
    def test_sends_bounded_structured_request_and_returns_raw_content(
        self, transport: MagicMock
    ) -> None:
        transport.return_value.__enter__.return_value.read.return_value = json.dumps(
            {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"kind":null}'}}
                ],
            }
        ).encode()
        self.assertEqual(self.provider.interpret(self.request), '{"kind":null}')
        http_request = transport.call_args.args[0]
        self.assertEqual(
            http_request.full_url, "https://openrouter.ai/api/v1/chat/completions"
        )
        self.assertEqual(http_request.get_header("Authorization"), "Bearer test-secret")
        payload = json.loads(http_request.data)
        self.assertIn(
            "json",
            payload["messages"][0]["content"].casefold(),
            "Qwen's JSON response mode requires JSON to be named in the prompt",
        )
        self.assertEqual(payload["model"], DEFAULT_MODEL)
        self.assertEqual(payload["max_tokens"], 768)
        self.assertTrue(payload["provider"]["require_parameters"])
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertEqual(
            json.loads(payload["messages"][1]["content"])["feedback"], "Video jumps"
        )
        self.assertNotIn("test-secret", http_request.data.decode())
        transport.assert_called_once()

    @patch("media_qc_agent.openrouter.urlopen")
    def test_transport_errors_are_sanitized_without_retries(
        self, transport: MagicMock
    ) -> None:
        for error in (
            HTTPError("secret-url", 401, "secret-body", Message(), None),
            URLError("secret-url"),
            TimeoutError("secret"),
        ):
            transport.reset_mock()
            transport.side_effect = error
            with self.assertRaises(ModelProviderError) as caught:
                self.provider.interpret(self.request)
            self.assertNotIn("secret", str(caught.exception))
            transport.assert_called_once()

    @patch("media_qc_agent.openrouter.urlopen")
    def test_incomplete_invalid_or_oversize_envelopes_fail(
        self, transport: MagicMock
    ) -> None:
        for raw in (
            b"invalid",
            b"{}",
            b"x" * 131_073,
            json.dumps(
                {
                    "choices": [
                        {"finish_reason": "length", "message": {"content": "partial"}}
                    ]
                }
            ).encode(),
        ):
            transport.return_value.__enter__.return_value.read.return_value = raw
            with (
                self.subTest(raw_length=len(raw)),
                self.assertRaises(ModelProviderError),
            ):
                self.provider.interpret(self.request)

    def test_configuration_is_explicit_and_overrideable(self) -> None:
        with patch.dict(
            "os.environ",
            {"OPENROUTER_API_KEY": "test-key", "OPENROUTER_MODEL": "test/model"},
            clear=True,
        ):
            self.assertEqual(
                OpenRouterModelProvider.from_environment()._model, "test/model"
            )
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(ValueError):
            OpenRouterModelProvider.from_environment()
        for timeout in (0, -1, float("nan")):
            with self.assertRaises(ValueError):
                OpenRouterModelProvider(api_key="test", timeout=timeout)
