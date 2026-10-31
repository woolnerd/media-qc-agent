"""Pull-request checks that need the PR's base, body, or changed files.

CI's pr-checks workflow runs `python scripts/pr_checks.py base` and
`python scripts/pr_checks.py diagrams` with BASE_REF, BASE_SHA, HEAD_SHA, and
PR_BODY set from the pull_request event. They run only on pull requests, so
they are not in scripts/check.sh.
"""

import os
import re
import subprocess
import sys

MAIN = "main"

# Files whose changes usually change a diagram in docs/diagrams.md: the core
# models (class diagram) and the SQLite tables (ER diagram).
DIAGRAM_SOURCES = (
    "src/media_qc_agent/domain/models.py",
    "src/media_qc_agent/workflow/models.py",
    "src/media_qc_agent/workflow/worker_models.py",
    "src/media_qc_agent/workflow/schema.py",
    "src/media_qc_agent/workflow/durable_provider.py",
)
DIAGRAMS = "docs/diagrams.md"
EXEMPTION = "Diagrams unchanged because"
# The PR template's list item with a reason after the colon.
EXEMPTION_LINE = re.compile(rf"^\s*[-*]\s*{EXEMPTION}[ \t]*:[ \t]*\S", re.MULTILINE)


def base_branch_error(base_ref: str) -> str | None:
    if base_ref == MAIN:
        return None
    return (
        f"This pull request targets {base_ref!r}, not {MAIN!r}. If {base_ref!r} "
        f"merges first, this PR merges into a dead branch and never reaches "
        f"{MAIN!r}. Change the base to {MAIN!r}; if it depends on another PR, "
        "state the merge order and rebase after that PR merges."
    )


def diagrams_error(changed: list[str], body: str) -> str | None:
    sources = sorted(set(changed).intersection(DIAGRAM_SOURCES))
    if not sources or DIAGRAMS in changed or EXEMPTION_LINE.search(body):
        return None
    return (
        f"{', '.join(sources)} changed but {DIAGRAMS} did not. Update the class "
        f'or ER diagram, or add "- {EXEMPTION}: <reason>" to the PR description.'
    )


def changed_files(base_sha: str, head_sha: str) -> list[str]:
    diff = subprocess.run(
        ["git", "diff", "--name-only", f"{base_sha}...{head_sha}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return diff.stdout.split()


def error_for(check: str) -> str | None:
    if check == "base":
        return base_branch_error(os.environ["BASE_REF"])
    if check == "diagrams":
        changed = changed_files(os.environ["BASE_SHA"], os.environ["HEAD_SHA"])
        return diagrams_error(changed, os.environ.get("PR_BODY", ""))
    raise SystemExit(f"unknown check {check!r}; use 'base' or 'diagrams'")


def main(argv: list[str]) -> int:
    check = argv[1] if len(argv) == 2 else ""
    error = error_for(check)
    if error:
        print(error, file=sys.stderr)
        return 1
    print(f"{check} check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
