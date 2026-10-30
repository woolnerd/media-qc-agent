"""Fail a pull request whose "Reviewed head commit" is not its head commit.

CI's review-head job runs this with PR_BODY and HEAD_SHA set from the
pull_request event. It checks only that the PR names the head commit as
reviewed; whether the review happened is still the owner's check.
"""

import os
import re
import sys

LABEL = "Reviewed head commit"
SHA = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)


def reviewed_shas(body: str) -> list[str]:
    """Return the commit-like tokens after the label's first colon."""

    for line in body.splitlines():
        label, colon, value = line.partition(":")
        if LABEL in label and colon:
            return [sha.lower() for sha in SHA.findall(value)]
    return []


def review_head_error(body: str, head_sha: str) -> str | None:
    head = head_sha.lower()
    shas = reviewed_shas(body)
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
