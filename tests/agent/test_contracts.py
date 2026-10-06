import unittest

from media_qc_agent.agent.contracts import (
    FakeModelProvider,
    InterpretationRequest,
    ModelIdentity,
    ModelProvider,
    prompt_version,
)
from media_qc_agent.domain.evidence import EvidenceInput, EvidenceRole


class ModelProviderTests(unittest.TestCase):
    def test_fake_is_repeatable_and_copies_fixtures(self) -> None:
        request = InterpretationRequest("Video jumps", ("video-1",))
        fixtures = {request: '{"kind":"visual_quality"}'}
        provider: ModelProvider = FakeModelProvider(fixtures)
        fixtures.clear()
        for _ in range(3):
            self.assertEqual(provider.interpret(request), '{"kind":"visual_quality"}')

    def test_lookup_binds_versions_and_evidence(self) -> None:
        request = InterpretationRequest("Video jumps", ("video-1",))
        provider = FakeModelProvider({request: "not JSON"})
        self.assertEqual(provider.interpret(request), "not JSON")
        for other in (
            InterpretationRequest("Video jumps", ("video-2",)),
            InterpretationRequest(
                "Video jumps",
                ("video-1",),
                (EvidenceInput(EvidenceRole.FACT, "video-1", "Motion jump"),),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "no synthetic"):
                provider.interpret(other)

    def test_rejects_invalid_context(self) -> None:
        for text, versions in ((" ", ("video-1",)), ("ok", ()), ("ok", ("",))):
            with self.assertRaises(ValueError):
                InterpretationRequest(text, versions)
        with self.assertRaisesRegex(ValueError, "unique"):
            InterpretationRequest("ok", ("video-1", "video-1"))
        with self.assertRaisesRegex(ValueError, "supplied"):
            InterpretationRequest(
                "ok",
                ("video-1",),
                (EvidenceInput(EvidenceRole.FACT, "video-2", "Jump"),),
            )

    def test_fake_identity_marks_fixture_replay(self) -> None:
        provider: ModelProvider = FakeModelProvider({})
        self.assertEqual(provider.identity, ModelIdentity("fake", "fixture", "fixture"))


class PromptVersionTests(unittest.TestCase):
    def test_version_is_a_stable_content_hash_with_a_readable_family(self) -> None:
        version = prompt_version("chat", {"system": "a", "schema": [1, 2]})
        self.assertEqual(
            version, prompt_version("chat", {"schema": [1, 2], "system": "a"})
        )
        family, digest = version.split(":")
        self.assertEqual(family, "chat")
        self.assertEqual(len(digest), 12)

    def test_any_content_or_family_change_produces_a_new_version(self) -> None:
        base = prompt_version("chat", {"system": "a"})
        self.assertNotEqual(base, prompt_version("chat", {"system": "a "}))
        self.assertNotEqual(base, prompt_version("jev", {"system": "a"}))

    def test_identity_rejects_blank_parts(self) -> None:
        for parts in (("", "m", "p"), ("p", " ", "p"), ("p", "m", "")):
            with self.subTest(parts=parts), self.assertRaises(ValueError):
                ModelIdentity(*parts)
