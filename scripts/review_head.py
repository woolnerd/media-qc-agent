"""Fail a pull request unless its "Reviewed head commit" still covers its head.

CI's review-head job runs this with PR_BODY, HEAD_SHA, and BASE_SHA set from the
pull_request event, in a checkout with full history. The line covers the head if
it names the head, or names a commit this PR added with only exempt files changed
since. It checks only what the PR names; whether the review happened, and whether
a follow-up fix was really minor, are still the owner's check.
"""

import os
import re
import subprocess
import sys

LABEL = "Reviewed head commit and reviewer/context"
# The PR template's list item, not prose or other bullets that mention the label.
LINE = re.compile(rf"^[ \t]*[-*][ \t]*{re.escape(LABEL)}:(.*)$", re.MULTILINE)
# The template's own hint is a comment, and an unclosed one hides the rest.
COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
SHA = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)
# Prose that can take a minor fix after its review. ADRs and everything under
# .github/ stay out: a late edit could change a decision or the reviewer's brief.
EXEMPT_FILES = frozenset({"AGENTS.md", "CONTRIBUTING.md", "README.md"})


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


def is_exempt(path: str) -> bool:
    if path in EXEMPT_FILES:
        return True
    return path.startswith("docs/") and not path.startswith("docs/adr/")


def reviewed_commit(body: str) -> str | None:
    """Return the first SHA on the line; later ones are only notes about it."""

    items = review_items(body)
    shas = SHA.findall(items[0]) if len(items) == 1 else []
    return shas[0].lower() if shas else None


def follow_up_problem(added_by_pr: bool, paths: list[str]) -> str | None:
    if not added_by_pr:
        return "That commit is not one this PR added."
    blocked = [path for path in paths if not is_exempt(path)]
    if blocked:
        return f"Files changed since then need a new review: {', '.join(blocked)}."
    return None


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False)


def is_added_by_pr(commit: str, head: str, base: str) -> bool:
    """Exit 1 means "not an ancestor"; anything else is an error, so it fails."""

    in_head = git("merge-base", "--is-ancestor", commit, head).returncode == 0
    return in_head and git("merge-base", "--is-ancestor", commit, base).returncode == 1


def changed_paths(commit: str, head: str) -> list[str]:
    """List both sides of a rename, so moving code into docs/ still counts."""

    diff = git("diff", "--name-only", "--no-renames", "-z", commit, head, "--")
    if diff.returncode != 0:
        raise RuntimeError(diff.stderr)
    return [path for path in diff.stdout.split("\0") if path]


def main() -> int:
    body = os.environ.get("PR_BODY", "")
    head = os.environ["HEAD_SHA"]
    error = review_head_error(body, head)
    if error is None:
        print(f"Review covers head commit {head[:7]}.")
        return 0
    commit = reviewed_commit(body)
    if commit:
        added = is_added_by_pr(commit, head, os.environ.get("BASE_SHA", ""))
        paths = changed_paths(commit, head) if added else []
        problem = follow_up_problem(added, paths)
        if problem is None:
            print(f"Review of {commit[:7]} covers {head[:7]}; unreviewed: {paths}")
            return 0
        error = f"{error} {problem}"
    print(error, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
