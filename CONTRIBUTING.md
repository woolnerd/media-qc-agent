# Contributing

## Workflow

- Work on a branch and merge through a pull request. Use conventional commits,
  for example `feat(workflow): deduplicate provider events`.
- Add tests for new behavior. For a bug, write a failing test first.
- Keep decisions in pure functions; keep database, provider, and other I/O at
  the edges. Keep provider-specific behavior behind interfaces.
- Never commit real customer data, private media, client artifacts, or
  credentials. All people, scripts, media, and provider behavior are synthetic.

## Checks

CI's `verify` job runs `scripts/check.sh` on every pull request and on `main`.
Run it before committing:

```bash
scripts/check.sh
```

The script uses `$PYTHON` if set, otherwise `.venv/bin/python` if it exists,
otherwise `python3`. Add a new check to the script, not to the workflow, so
local runs and CI stay identical.

The script starts with a [gitleaks](https://github.com/gitleaks/gitleaks)
secret scan of the committed history and of staged and unstaged changes, so
install gitleaks first (`brew install gitleaks`). It skips untracked files such
as `.env`. If it flags something that is not a secret, add the narrowest
allowlist regex you can to `.gitleaks.toml` and explain it in the PR. If it
flags a real secret, rotate it; removing it from a later commit leaves it in
history.

The script also checks the Quint model in `specs/` with the
[Quint](https://quint-lang.org) simulator, so install Node.js 20 or newer. It
runs a pinned Quint through `npx`; the first run downloads it.

Ruff limits cyclomatic complexity to 6 per function. Split decision logic that
exceeds it.

The evaluation snapshots change only on purpose. To regenerate one, run its
`compare` or `baseline` command from `scripts/check.sh` without `--expect`,
redirect stdout to the snapshot, and explain the diff in the PR. Changing the chat prompt, schema, or default model makes a test
fail until new live runs are recorded. Paid live runs need the owner's approval and refuse to start unless `MEDIA_QC_ALLOW_PAID=1` is set.

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

## Review before merge

Fill in the PR template. It asks which [guarantees](README.md#guarantees) the
change affects, which tests challenge them, and what happens if execution stops
halfway.

Every PR also gets an adversarial review of its current head commit, in a fresh
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
  too. The `review-head` workflow fails until the PR's "Reviewed head commit"
  line names the current head commit (short or full SHA). It re-runs when the
  description is edited, and it runs only on pull requests, so it is not in
  `scripts/check.sh`.

Merge when CI passes and no confirmed P0 or P1 finding is open. A ruleset on
`main` enforces the first gate: changes arrive through a pull request, the
`verify` and `review-head` jobs must pass, and force-push and deletion are
blocked. The owner checks the review gate; `review-head` checks only that the
recorded commit is current.

## Documentation

- The README describes current behavior. Keep it accurate in the same PR as
  the change.
- ADRs in `docs/adr/` record consequential decisions. Do not rewrite an
  accepted ADR; supersede it with a new one.
- Active work and acceptance criteria live in GitHub issues.
- Prefer a clear name or a short docstring to a separate document.
