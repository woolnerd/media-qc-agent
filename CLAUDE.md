# Agent notes

Read `CONTRIBUTING.md` before changing code. Run `scripts/check.sh` before
every commit; it is the same command CI runs.

## Rules and what enforces them

When a rule has no enforcer, prefer adding one (architecture, type, lint, test,
or CI check) over adding more prose. When an agent is corrected for breaking a
rule, add or update its row here in the same change.

| Rule | Enforced by |
| --- | --- |
| Changes reach `main` only through a pull request | GitHub ruleset on `main` |
| CI's `verify` job passes before merge | GitHub ruleset on `main` (required check) |
| Local checks match CI | `scripts/check.sh`, called by `.github/workflows/ci.yml` |
| Cyclomatic complexity at most 6 per function | ruff (`ruff.toml`) |
| Formatting and types are clean | `ruff format --check`, `mypy` in `scripts/check.sh` |
| Evaluation snapshots change only on purpose | `--expect` comparisons in `scripts/check.sh` |
| Prompt, schema, or default model changes need new live runs | `test_saved_runs_use_the_current_chat_prompt` in `tests/cli/test_baseline.py` |
| Paid live runs need the owner's approval | nothing yet (prose only) |
| Adversarial review covers the current head commit | `review-head` workflow checks the PR names the head commit (`scripts/review_head.py`); the owner checks the review itself |
| No real customer data or credentials committed | `.gitignore` for `.env`; otherwise prose only |
