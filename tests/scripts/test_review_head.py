import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.review_head import is_exempt, resolve_commit, review_head_error

GATE = Path(__file__).resolve().parents[2] / "scripts" / "review_head.py"

HEAD = "4a43c4b9c928cd004306251ac19ed9228654fb83"


def body(line: str) -> str:
    return f"## Adversarial review\n\n{line}\n- Verdict: no confirmed blockers found\n"


class ReviewHeadTests(unittest.TestCase):
    def test_short_or_full_head_sha_passes(self) -> None:
        for sha in (HEAD[:7], HEAD[:12], HEAD, HEAD.upper()[:9]):
            with self.subTest(sha=sha):
                line = f"- Reviewed head commit and reviewer/context: {sha}, fresh"
                self.assertIsNone(review_head_error(body(line), HEAD))

    def test_backticked_sha_passes(self) -> None:
        line = f"- Reviewed head commit and reviewer/context: `{HEAD[:7]}` (Opus)"
        self.assertIsNone(review_head_error(body(line), HEAD))

    def test_older_commit_fails(self) -> None:
        line = "- Reviewed head commit and reviewer/context: 0265958, fresh context"
        error = review_head_error(body(line), HEAD)
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("0265958", error)
        self.assertIn(HEAD[:7], error)

    def test_missing_or_blank_line_fails(self) -> None:
        for text in (
            "",
            body("- Verdict: pending"),
            body("- Reviewed head commit and reviewer/context:"),
            body("- Reviewed head commit and reviewer/context: self-review only"),
        ):
            with self.subTest(text=text):
                self.assertIsNotNone(review_head_error(text, HEAD))

    def test_short_hex_and_sha_elsewhere_do_not_count(self) -> None:
        for text in (
            body(f"- Reviewed head commit and reviewer/context: {HEAD[:6]}"),
            f"CI ran on {HEAD[:7]}.\n"
            + body("- Reviewed head commit and reviewer/context: pending"),
        ):
            with self.subTest(text=text):
                self.assertIsNotNone(review_head_error(text, HEAD))

    def test_prose_mentioning_the_label_is_not_the_line(self) -> None:
        text = (
            'This fails unless the "Reviewed head commit" line names it: see below.\n'
            + body(f"- Reviewed head commit and reviewer/context: {HEAD[:7]}")
        )
        self.assertIsNone(review_head_error(text, HEAD))

    def test_sha_must_be_a_prefix_not_a_substring(self) -> None:
        line = f"- Reviewed head commit and reviewer/context: {HEAD[1:9]}"
        self.assertIsNotNone(review_head_error(body(line), HEAD))

    def test_similar_bullet_cannot_stand_in_for_the_real_line(self) -> None:
        enforcer = f"- Reviewed head commit check was stale on #70: {HEAD[:7]}\n\n"
        for line in (
            "- Reviewed head commit and reviewer/context:",
            "- Reviewed head commit and reviewer/context: 0265958",
        ):
            with self.subTest(line=line):
                error = review_head_error(enforcer + body(line), HEAD)
                self.assertIsNotNone(error)

    def test_similar_bullet_does_not_block_the_real_line(self) -> None:
        enforcer = "- Reviewed head commit check accepted 0265958: add a test\n\n"
        line = f"- Reviewed head commit and reviewer/context: {HEAD[:7]}"
        self.assertIsNone(review_head_error(enforcer + body(line), HEAD))

    def test_sha_in_a_comment_does_not_count(self) -> None:
        for comment in (f"<!-- {HEAD[:7]} -->", f"<!-- {HEAD[:7]}"):
            with self.subTest(comment=comment):
                line = f"- Reviewed head commit and reviewer/context: {comment}"
                self.assertIsNotNone(review_head_error(body(line), HEAD))

    def test_sha_after_a_comment_counts(self) -> None:
        line = f"- Reviewed head commit and reviewer/context: <!-- SHA --> {HEAD[:7]}"
        self.assertIsNone(review_head_error(body(line), HEAD))

    def test_item_cannot_span_lines(self) -> None:
        text = f"-\n\nReviewed head commit and reviewer/context: {HEAD[:7]}\n"
        self.assertIsNotNone(review_head_error(text, HEAD))

    def test_two_real_lines_fail_even_if_one_is_current(self) -> None:
        line = (
            f"- Reviewed head commit and reviewer/context: {HEAD[:7]}\n"
            "- Reviewed head commit and reviewer/context: 0265958"
        )
        error = review_head_error(body(line), HEAD)
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("2", error)

    def test_windows_line_endings_pass(self) -> None:
        line = f"- Reviewed head commit and reviewer/context: {HEAD[:7]}"
        text = body(line).replace("\n", "\r\n")
        self.assertIsNone(review_head_error(text, HEAD))


class ExemptPathTests(unittest.TestCase):
    def test_general_docs_and_working_guides_are_exempt(self) -> None:
        for path in (
            "README.md",
            "AGENTS.md",
            "CONTRIBUTING.md",
            "docs/diagrams.md",
            "docs/guides/setup.md",
        ):
            with self.subTest(path=path):
                self.assertTrue(is_exempt(path))

    def test_decisions_review_instructions_and_code_are_not(self) -> None:
        for path in (
            "docs/adr/0029-check.md",
            "docs/adr/README.md",
            ".github/prompts/adversarial-review.md",
            ".github/pull_request_template.md",
            ".github/workflows/review-head.yml",
            "CLAUDE.md",
            "scripts/review_head.py",
            "specs/submission.qnt",
            "src/media_qc_agent/workflow/worker.py",
            "tests/README.md",
            "src/README.md",
            "docs.md",
            "docs/ADR/0029-check.md",
        ):
            with self.subTest(path=path):
                self.assertFalse(is_exempt(path))


class ResolveCommitTests(unittest.TestCase):
    def test_a_sha_abbreviating_two_objects_resolves_to_nothing(self) -> None:
        # A real 7-character collision is too slow to create in a test.
        listing = subprocess.CompletedProcess([], 0, stdout="abc\ndef\n", stderr="")
        with mock.patch("scripts.review_head.git", return_value=listing):
            self.assertIsNone(resolve_commit("abcdef0"))


class FollowUpCommitTests(unittest.TestCase):
    """Run the gate against a real repository, as the workflow does."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        self.git("init", "-q", "-b", "main")
        self.base = self.commit("src/app.py")
        self.git("checkout", "-q", "-b", "feature")
        self.reviewed = self.commit("src/app.py")

    def git(self, *args: str) -> str:
        identity = ("-c", "user.name=Test", "-c", "user.email=test@example.com")
        result = subprocess.run(
            ["git", *identity, *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def commit(self, *paths: str) -> str:
        for path in paths:
            file = self.repo / path
            file.parent.mkdir(parents=True, exist_ok=True)
            with file.open("a") as handle:
                handle.write("change\n")
            self.git("add", path)
        self.git("commit", "-q", "-m", "change")
        return self.git("rev-parse", "HEAD")

    def gate(self, named: str, head: str, base: str | None = None) -> int:
        line = f"- Reviewed head commit and reviewer/context: {named}"
        environment = {
            **os.environ,
            "PR_BODY": body(line),
            "HEAD_SHA": head,
            "BASE_SHA": self.base if base is None else base,
        }
        result = subprocess.run(
            [sys.executable, str(GATE)],
            cwd=self.repo,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotIn("Traceback", result.stderr)
        return result.returncode

    def test_naming_the_head_needs_no_history(self) -> None:
        self.assertEqual(self.gate(self.reviewed, self.reviewed), 0)

    def test_exempt_follow_up_passes(self) -> None:
        for path in ("README.md", "AGENTS.md", "CONTRIBUTING.md", "docs/guide.md"):
            with self.subTest(path=path):
                self.assertEqual(self.gate(self.reviewed, self.commit(path)), 0)

    def test_short_reviewed_sha_passes(self) -> None:
        head = self.commit("README.md")
        self.assertEqual(self.gate(self.reviewed[:7], head), 0)

    def test_follow_up_outside_the_exempt_files_fails(self) -> None:
        for path in (
            "src/app.py",
            "docs/adr/0001-decision.md",
            ".github/prompts/adversarial-review.md",
            "CLAUDE.md",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.gate(self.reviewed, self.commit(path)), 1)

    def test_one_exempt_and_one_other_file_fails(self) -> None:
        head = self.commit("README.md", "src/app.py")
        self.assertEqual(self.gate(self.reviewed, head), 1)

    def test_an_earlier_code_change_is_not_hidden_by_a_later_docs_commit(self) -> None:
        self.commit("src/app.py")
        head = self.commit("README.md")
        self.assertEqual(self.gate(self.reviewed, head), 1)

    def test_moving_code_into_docs_fails(self) -> None:
        (self.repo / "docs").mkdir()
        self.git("mv", "src/app.py", "docs/app.md")
        self.git("commit", "-q", "-m", "move")
        self.assertEqual(self.gate(self.reviewed, self.git("rev-parse", "HEAD")), 1)

    def test_base_commit_cannot_stand_in_for_a_review(self) -> None:
        self.git("checkout", "-q", "main")
        self.git("checkout", "-q", "-b", "docs-only")
        head = self.commit("README.md")
        self.assertEqual(self.gate(self.base, head), 1)

    def test_commit_outside_the_head_history_fails_even_if_only_docs_differ(
        self,
    ) -> None:
        self.git("checkout", "-q", "-b", "rewritten")
        rewritten = self.commit("README.md")
        self.git("checkout", "-q", "feature")
        head = self.commit("docs/guide.md")
        self.assertEqual(self.gate(rewritten, head), 1)

    def test_unknown_commit_fails(self) -> None:
        head = self.commit("README.md")
        self.assertEqual(self.gate("0000000", head), 1)

    def test_missing_base_fails(self) -> None:
        head = self.commit("README.md")
        self.assertEqual(self.gate(self.reviewed, head, base=""), 1)

    def test_only_the_first_sha_is_the_reviewed_commit(self) -> None:
        docs = self.commit("src/app.py")
        head = self.commit("README.md")
        named = f"{self.reviewed}, first review; later note on {docs}"
        self.assertEqual(self.gate(named, head), 1)

    def test_a_ref_named_like_the_reviewed_sha_cannot_replace_it(self) -> None:
        self.commit("src/app.py")
        head = self.commit("README.md")
        short = self.reviewed[:7]
        for kind in ("heads", "tags"):
            with self.subTest(kind=kind):
                self.git("update-ref", f"refs/{kind}/{short}", head)
                self.assertEqual(self.gate(short, head), 1)
                self.git("update-ref", "-d", f"refs/{kind}/{short}")

    def test_a_submodule_bump_fails_even_when_gitmodules_ignores_it(self) -> None:
        (self.repo / ".gitmodules").write_text(
            '[submodule "vendor"]\n\tpath = vendor\n\turl = ./x\n\tignore = all\n'
        )
        self.git("add", ".gitmodules")
        self.git("update-index", "--add", "--cacheinfo", f"160000,{self.base},vendor")
        self.git("commit", "-q", "-m", "add submodule")
        reviewed = self.git("rev-parse", "HEAD")
        self.git("update-index", "--cacheinfo", f"160000,{reviewed},vendor")
        self.git("commit", "-q", "-m", "bump submodule")
        self.assertEqual(self.gate(reviewed, self.git("rev-parse", "HEAD")), 1)

    def test_a_link_or_executable_at_an_exempt_path_fails(self) -> None:
        for mode, path in (
            ("120000", "AGENTS.md"),
            ("160000", "docs/sub"),
            ("100755", "docs/run.md"),
        ):
            with self.subTest(path=path):
                self.git("reset", "-q", "--hard", self.reviewed)
                self.git(
                    "update-index", "--add", "--cacheinfo", f"{mode},{self.base},{path}"
                )
                self.git("commit", "-q", "-m", "special file")
                self.assertEqual(
                    self.gate(self.reviewed, self.git("rev-parse", "HEAD")), 1
                )

    def test_merging_main_that_changed_only_exempt_files_passes(self) -> None:
        self.git("checkout", "-q", "main")
        self.base = self.commit("AGENTS.md")
        self.git("checkout", "-q", "feature")
        self.git("merge", "-q", "--no-edit", "main")
        self.assertEqual(self.gate(self.reviewed, self.git("rev-parse", "HEAD")), 0)

    def test_merging_main_after_the_review_needs_a_new_review(self) -> None:
        self.git("checkout", "-q", "main")
        self.base = self.commit("src/other.py")
        self.git("checkout", "-q", "feature")
        self.git("merge", "-q", "--no-edit", "main")
        self.assertEqual(self.gate(self.reviewed, self.git("rev-parse", "HEAD")), 1)


if __name__ == "__main__":
    unittest.main()
