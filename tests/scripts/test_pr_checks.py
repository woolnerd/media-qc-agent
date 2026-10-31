import unittest

from scripts.pr_checks import (
    DIAGRAM_SOURCES,
    DIAGRAMS,
    base_branch_error,
    diagrams_error,
)

SCHEMA = "src/media_qc_agent/workflow/schema.py"


class BaseBranchTests(unittest.TestCase):
    def test_main_passes(self) -> None:
        self.assertIsNone(base_branch_error("main"))

    def test_feature_branch_fails_and_names_it(self) -> None:
        # PR #70 targeted #69's branch and merged into it after #69 merged.
        error = base_branch_error("claude/check-script")
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("claude/check-script", error)
        self.assertIn("'main'", error)

    def test_lookalike_names_fail(self) -> None:
        for ref in ("Main", "main-v2", "release/main", ""):
            with self.subTest(ref=ref):
                self.assertIsNotNone(base_branch_error(ref))


class DiagramsTests(unittest.TestCase):
    def test_unrelated_changes_pass(self) -> None:
        self.assertIsNone(diagrams_error(["README.md", "src/x.py"], ""))

    def test_every_source_without_diagrams_fails(self) -> None:
        for source in DIAGRAM_SOURCES:
            with self.subTest(source=source):
                error = diagrams_error([source, "tests/t.py"], "")
                self.assertIsNotNone(error)
                assert error is not None
                self.assertIn(source, error)

    def test_source_with_diagrams_passes(self) -> None:
        self.assertIsNone(diagrams_error([SCHEMA, DIAGRAMS], ""))

    def test_exemption_with_reason_passes(self) -> None:
        for line in (
            "- Diagrams unchanged because: renamed a private helper",
            "* Diagrams unchanged because : no field or table changed",
        ):
            with self.subTest(line=line):
                body = f"## Architecture\n\n{line}\n"
                self.assertIsNone(diagrams_error([SCHEMA], body))

    def test_blank_or_prose_exemption_fails(self) -> None:
        for body in (
            "- Diagrams unchanged because:\n- Next item: x",
            "- Diagrams unchanged because:   \n",
            "The diagrams unchanged because nothing moved.",
            'Add "- Diagrams unchanged because: <reason>" if needed.',
        ):
            with self.subTest(body=body):
                self.assertIsNotNone(diagrams_error([SCHEMA], body))

    def test_path_must_match_exactly(self) -> None:
        self.assertIsNone(diagrams_error([f"tests/{SCHEMA}"], ""))
        self.assertIsNone(diagrams_error([f"docs/old/{DIAGRAMS}", "README.md"], ""))
