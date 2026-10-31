# Contributing

[`AGENTS.md`](AGENTS.md) is the contributor guide for agents and people alike:
setup, checks, choosing tests, pull requests, review, and the table of rules
and what enforces them. Start there.

## Owner decisions

These stay with the repository owner:

- **Paid live runs.** Approve each one. Live runs refuse to start unless
  `MEDIA_QC_ALLOW_PAID=1` is set.
- **The review gate.** Check that the adversarial review covers the current
  head commit and that no confirmed P0 or P1 finding is open. The `review-head`
  check confirms only that the recorded commit is current, not that the review
  was done well.
- **Merging.** Merge when CI passes and the review gate is met. A ruleset on
  `main` requires a pull request and the `verify` and `review-head` checks,
  and blocks force-push and deletion.
- **Rotating leaked secrets.** If gitleaks flags a real secret, rotate it.
