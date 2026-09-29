# Quality gates

Review should establish the architecture, its guarantees, and evidence that
those guarantees survive failures. Authors still own implementation correctness.
An LLM review provides additional evidence; it does not certify the change.

## Before merge

| Gate | Evidence | Enforcement today |
| --- | --- | --- |
| Static checks | Compilation, Ruff, formatting, mypy; complexity at most 6 per function | Existing CI `verify` job |
| Behavior | Full tests, synthetic demo, and offline evaluation fixtures; new behavior has tests; bug fixes start with a failing reproduction | CI runs the checks; reviewer checks relevance |
| Architecture | Completed PR template, affected invariant IDs, alternatives, limitations, and a diagram when boundaries or state transitions change | Human review |
| Failure scenarios | Tests for the affected crash, timeout, concurrency, stale-input, or callback sequences | Tests run in CI; reviewer checks scenario selection |
| Adversarial review | A fresh review of the current commit, recorded findings and dispositions | Review protocol; no automatic LLM job |
| Merge decision | Current CI succeeds; no unresolved confirmed P0/P1 findings; remaining risks have an explicit disposition | Repository owner checks before merging |

These review gates are project policy. A PR template does not enforce its own
completion, and a green test job does not prove independent review occurred.
Repository rules must require checks/reviews to make merge blocking enforceable
through GitHub; the current workflow relies on the owner's merge decision.

## Select tests from the changed boundary

- Approval or artifact binding: stale approvals, stale editors, exact version
  matching, and input rebinding.
- Submission: edit/reservation ordering with independent database connections,
  committed reservation visibility, and lost acceptance responses.
- Completion or retries: duplicate events, obsolete jobs, callback ordering,
  and immutable input lineage.
- Model output or policy: malformed output, embedded instructions, unrelated
  evidence, abstention, creative choice, and excessive repair scope.

Use controlled events or barriers for concurrency tests, with bounded waits.
A SQLite lock timeout must fail the test; it is not a valid policy rejection.
Document what an artificial scheduling pause controls. Avoid sleep-based races.

When a prompt, model, schema, or classification policy changes, run the offline
evaluation cases and explain whether a live model comparison is needed. Fixture
replay cannot establish model accuracy. Run paid live comparisons only when the
user has authorized them, and report unsafe decisions separately from safe
abstentions.

## Adversarial review procedure

Use [the review prompt](../.github/prompts/adversarial-review.md) in a fresh context.
Supply the issue/requirements, diff, base and head commit IDs, relevant surrounding
code, and [architecture invariants](architecture-invariants.md). Do not seed the
review with the author's claim that the implementation is correct.

For consequential changes, examine correctness, security, and maintainability
as separate passes. Another model can add a perspective, but agreement between
reviewers is not proof of correctness.

Record the reviewed commit and distinguish confirmed findings from hypotheses.
For each confirmed defect, record severity, triggering sequence, code location,
and a regression test where practical. Fix P0/P1 findings before merging.
P2 findings need a fix or a linked issue and an explicit rationale for deferral.
Speculative concerns require investigation or a stated unresolved limitation;
do not promote them into confirmed defects without evidence.

After fixes, verify the reproduction fails on the faulty version and passes on
the fixed version, rerun affected checks, and have the reviewer examine the fix.
New commits must be considered before the final verdict; an older review is not
approval of a later implementation. Record skipped review passes with a reason.

## Architecture review for the owner

The PR summary should let the owner answer:

1. Which component owns each decision and side effect?
2. What guarantee changed, and which test challenges it?
3. What happens if execution stops halfway through?
4. Why was this design chosen over the alternatives?
5. What remains unsupported, and what does it cost us?

Pause a merge when these questions have unclear answers. Security scanners,
mutation testing, and distributed worker tests can be added as separate changes
when their scope and enforcement are defined.
