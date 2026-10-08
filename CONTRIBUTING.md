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

CI's `verify` job runs these on every pull request and on `main`. Run them
before committing:

```bash
PYTHONPATH=src python3 -m compileall -q src tests
PYTHONPATH=src python3 -m ruff check src tests
PYTHONPATH=src python3 -m ruff format --check src tests
PYTHONPATH=src python3 -m mypy src tests
PYTHONPATH=src python3 -m unittest discover -s tests -t .
PYTHONPATH=src python3 -m media_qc_agent.cli.demo
PYTHONPATH=src python3 -m media_qc_agent.cli.evaluate --mask-version-labels
PYTHONPATH=src python3 -m media_qc_agent.cli.compare --expect evals/results/policy-comparison-v1.json
PYTHONPATH=src python3 -m media_qc_agent.cli.baseline --expect evals/results/baseline-comparison-v1.json
```

Ruff limits cyclomatic complexity to 6 per function. Split decision logic that
exceeds it.

The evaluation snapshots change only on purpose. To regenerate one, run the
command without `--expect`, redirect stdout to the snapshot, and explain the
diff in the PR. Changing the chat prompt, schema, or default model makes a test
fail until new live runs are recorded. Paid live runs need the owner's approval.

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
  too.

Merge when CI passes and no confirmed P0 or P1 finding is open. GitHub does not
enforce these gates on this repository's plan; the owner checks them.

## Documentation

- The README describes current behavior. Keep it accurate in the same PR as
  the change.
- ADRs in `docs/adr/` record consequential decisions. Do not rewrite an
  accepted ADR; supersede it with a new one.
- Active work and acceptance criteria live in GitHub issues.
- Prefer a clear name or a short docstring to a separate document.
