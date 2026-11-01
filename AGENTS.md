# Agent and contributor guide

This file is the working guide for anyone changing this repository, agent or
person. Claude Code loads it through `CLAUDE.md`; other agents load it
directly. `CONTRIBUTING.md` lists only the decisions reserved for the owner.

## Before you start

- Run `git status --short` before switching branches. Uncommitted changes you
  don't recognise may be another session's, or your own from turns no longer
  in context. Don't switch, stash, or commit them; find out where they came
  from first. Use a separate worktree for parallel work.
- Branch from `origin/main` and open the pull request against `main`. Don't
  stack a PR on another feature branch: if the lower PR merges first, GitHub
  can merge the upper one into the dead branch, and its commits never reach
  `main`. To check whether a commit landed, run
  `git branch -r --contains <sha>` and look for `origin/main`.
- Use conventional commits, for example
  `feat(workflow): deduplicate provider events`.

## Code

- Add tests for new behavior. For a bug, write a failing test first.
- Keep decisions in pure functions; keep database, provider, and other I/O at
  the edges. Keep provider-specific behavior behind interfaces.
- Ruff limits cyclomatic complexity to 6 per function. Split decision logic
  that exceeds it.
- Never commit real customer data, private media, client artifacts, or
  credentials. All people, scripts, media, and provider behavior are synthetic.

## Checks

Run `scripts/check.sh` before every commit. CI's `verify` job runs the same
script on every pull request and on `main`. The script uses `$PYTHON` if set,
otherwise `.venv/bin/python` if it exists, otherwise `python3`. Add a new check
to the script, not to the workflow, so local runs and CI stay identical.

Setup the script needs:

- [gitleaks](https://github.com/gitleaks/gitleaks) (`brew install gitleaks`).
  The script starts by scanning committed history and staged and unstaged
  changes. It skips untracked files such as `.env`. If it flags something that
  is not a secret, add the narrowest allowlist regex you can to
  `.gitleaks.toml` and explain it in the PR. If it flags a real secret, tell
  the owner to rotate it; removing it in a later commit leaves it in history.
- Node.js 20 or newer, for the [Quint](https://quint-lang.org) simulator. The
  script installs the Quint locked in `tools/quint/package-lock.json` with
  `npm ci --ignore-scripts` on every run, which checks every package against
  its lockfile hash. It allows only `quint typecheck` and `quint run` on the
  TypeScript backend, so no native binary is downloaded. To upgrade Quint,
  change `tools/quint/package.json` and run
  `npm install --prefix tools/quint --package-lock-only --ignore-scripts`. In
  the PR, review the lockfile diff for new packages, `resolved` URLs outside
  `https://registry.npmjs.org/`, and new `hasInstallScript` entries, and
  confirm `scripts/check.sh` still passes on the TypeScript backend.

Evaluation snapshots change only on purpose. To regenerate one, run its
`compare` or `baseline` command from `scripts/check.sh` without `--expect`,
redirect stdout to the snapshot, and explain the diff in the PR. Changing the
chat prompt, schema, or default model makes a test fail until new live runs
are recorded.

Live runs are billed. They refuse to start unless `MEDIA_QC_ALLOW_PAID=1` is
set. Never set it unless the owner approved that specific run in the current
conversation.

## Choosing tests

Test the boundary you changed:

- **Approval or binding:** stale approvals, stale editors, exact version
  matching, and rebinding inputs.
- **Submission:** edit/reservation ordering on separate connections, and lost
  acceptance responses.
- **Completion or retries:** duplicate events, obsolete jobs, callback order,
  and lineage.
- **Model output or policy:** malformed output, embedded instructions,
  unrelated evidence, abstention, creative choices, and excessive scope.

A change to claims, leases, submission, completion, or retry also updates the
Hypothesis state machine in `tests/workflow/test_submission_properties.py` and
the Quint model in `specs/submission.qnt`. Before relying on a new invariant,
break the code (or the model) on purpose and confirm the check fails. A
Hypothesis failure prints the shortest step sequence that reproduces it; a
Quint failure prints its trace and a seed.

For concurrency, use controlled events or barriers with bounded waits, not
sleeps. A SQLite lock timeout must fail the test; it is not a policy rejection.

## Pull requests and review

Fill in the PR template. It asks which [guarantees](README.md#guarantees) the
change affects, which tests challenge them, and what happens if execution stops
halfway.

Every PR gets an adversarial review of its current head commit, in a fresh
context, using [the review prompt](.github/prompts/adversarial-review.md).
Give the reviewer the issue, the diff, the base and head commits, and the
guarantees, but not the author's claim that the change is correct. For
consequential changes, review correctness, security, and maintainability as
separate passes, and record any skipped pass with a reason.

- Separate confirmed findings from hypotheses. A confirmed finding records its
  severity, the triggering sequence, the location, and a regression test where
  practical.
- Fix P0 and P1 findings before merging. A P2 needs a fix, or a linked issue
  and a reason to defer.
- A review covers only the commit it saw. Fixes made afterwards need review
  too.
- The PR description's "Reviewed head commit" line names the commit a review
  covered (short or full SHA). Update it only after a review of the new head
  commit; never set it to a commit no review has seen. The required
  `review-head` check fails until the line matches the head, so a push without
  a new review leaves the check failing, as intended. Editing the description
  re-runs the check. It runs only on pull requests, so it is not in
  `scripts/check.sh`.

## Documentation

- The README describes current behavior. Keep it accurate in the same PR as
  the change.
- ADRs in `docs/adr/` record consequential decisions. Do not rewrite an
  accepted ADR; supersede it with a new one.
- Keep `docs/diagrams.md` in sync when models or tables change.
- Active work and acceptance criteria live in GitHub issues.
- Prefer a clear name or a short docstring to a separate document.

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
| Worker and completion guarantees hold beyond hand-written orderings | Hypothesis state machine `tests/workflow/test_submission_properties.py` and Quint model `specs/submission.qnt`, both in `scripts/check.sh` |
| Paid live runs need the owner's approval | `require_paid_run_approval` in `agent/openrouter.py` (`MEDIA_QC_ALLOW_PAID=1`); agents never set it without the owner's approval in the current conversation |
| Adversarial review covers the current head commit | `review-head` workflow, a required check on `main`, fails until the PR names its head commit (`scripts/review_head.py`); the owner checks the review itself |
| gitleaks in CI and Quint come from pinned, hash-checked sources (Python check tools are pinned without hashes) | SHA-256 check for gitleaks in `.github/workflows/ci.yml`; `npm ci --ignore-scripts` against `tools/quint/package-lock.json` and the backend guard in `scripts/check.sh` |
| No credentials committed | gitleaks in `scripts/check.sh` (history, staged, and unstaged changes; `.gitleaks.toml`) |
| No real customer data committed | `.gitignore` for `.env`; otherwise prose only |
| PRs target `main`, not another feature branch | prose only |
