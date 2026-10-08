## Outcome

What behavior changes, and why? Link the issue or describe the acceptance criteria.

## Architecture and tradeoffs

- Components that own decisions, durable state, and external side effects:
- Affected guarantee IDs (README "Guarantees"):
- Chosen approach, alternative considered, and reason:
- Remaining limitations and operational cost:

Include a small diagram when ownership or state transitions change. For a change
with no architectural effect, say why a diagram and ADR are unnecessary.

## Failure scenarios and evidence

| Scenario / invariant | Test or reproduction | Result and limits |
| --- | --- | --- |
| | | |

- CI result for the current head:
- Relevant documentation / ADR updates:
- Model changes: offline evaluation result and live comparison, or reason omitted:

## Adversarial review

Follow "Review before merge" in `CONTRIBUTING.md` and `.github/prompts/adversarial-review.md`.

- Reviewed head commit and reviewer/context:
- Correctness, security, maintainability passes completed or skipped with reason:
- Confirmed findings, fixes, and regression evidence:
- Unverified concerns / remaining risks and dispositions:
- Verdict: pending, blockers found, or no confirmed blockers found:

## Merge checks

- [ ] Current CI succeeds.
- [ ] Affected invariants and failure scenarios have relevant evidence.
- [ ] Required architecture/documentation updates are present.
- [ ] Adversarial review covers the current implementation; no unresolved P0/P1 findings.
- [ ] Remaining confirmed findings are fixed or have a linked issue and deferral rationale.
