import unittest

from scripts.review_head import review_head_error

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


if __name__ == "__main__":
    unittest.main()
