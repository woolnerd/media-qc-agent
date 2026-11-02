"""Fail a pull request whose "Reviewed head commit" is not its head commit.

CI's review-head job runs this with PR_BODY and HEAD_SHA set from the
pull_request event. It checks only that the PR names the head commit as
reviewed; whether the review happened is still the owner's check.
"""

import os
import re
import sys

LABEL = "Reviewed head commit and reviewer/context"
# The PR template's list item, not prose or other bullets that mention the label.
LINE = re.compile(rf"^[ \t]*[-*][ \t]*{re.escape(LABEL)}:(.*)$", re.MULTILINE)
# The template's own hint is a comment, and an unclosed one hides the rest.
COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
SHA = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)


def review_items(body: str) -> list[str]:
    """Return the text of each "Reviewed head commit" item, ignoring comments."""

    return LINE.findall(COMMENT.sub("", body))


def review_head_error(body: str, head_sha: str) -> str | None:
    items = review_items(body)
    if len(items) > 1:
        return (
            f'"{LABEL}" appears {len(items)} times. '
            "Keep only the one from the PR template."
        )
    head = head_sha.lower()
    shas = [sha.lower() for sha in SHA.findall(items[0])] if items else []
    if any(head.startswith(sha) for sha in shas):
        return None
    named = ", ".join(shas) or "no commit"
    return (
        f'"{LABEL}" names {named}, but the head commit is {head[:7]}. '
        "Review the head commit and record it in the PR description."
    )


def main() -> int:
    error = review_head_error(os.environ.get("PR_BODY", ""), os.environ["HEAD_SHA"])
    if error:
        print(error, file=sys.stderr)
        return 1
    print(f"Review covers head commit {os.environ['HEAD_SHA'][:7]}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
