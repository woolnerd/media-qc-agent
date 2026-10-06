# ADR 0025: Compare acceptance policies on recorded outputs

## Context

Issue 21 requires a CI evaluation command that compares prompt or policy
versions and tracks false passes alongside false blocks. CI cannot make paid
model calls, and fixture responses are written to pass. The saved September 29
live runs contain real model outputs, including one wrong but policy-valid
caption repair for a TTS defect, but they were stored in three formats.

## Decision

Version the application's acceptance policy separately from the prompt and pass
it through validation, tracing, and evaluation, keeping `confidence-v1` as the
default. Add `grounded-scope-v2` as a candidate that requires a cited fact on an
artifact the repair replaces. Normalize the saved live outputs into one
recording file, checked against its sources by a test. Replay it and the
fixtures under every policy and classify each failure as a false pass, false
block, blocked wrong output, or other failure. Bind each recorded output to a
digest of the request it answered. CI compares the full report to a checked-in snapshot.

## Alternatives rejected

- Compare prompt versions: needs paid live calls for every prompt, which CI
  cannot make. Policy replay is deterministic and free.
- Threshold-only gates (for example, "zero false passes"): would either fail on
  known recorded errors or hide new ones. A snapshot shows every change.
- Make `grounded-scope-v2` the default now: it removed the recorded false passes
  without new false blocks, but no saved case yet tests a correct diagnosis that
  cites only out-of-scope facts; adoption is a separate product decision.
- Parse the three result formats at replay time: spreads format handling into
  the evaluator; normalization with a provenance test keeps it in one place.

## Consequences

Any change to validation, scoring, or a policy that changes a recorded outcome
fails CI until the snapshot is regenerated, which makes the change visible in
the PR diff. Prompt changes still require a live run before they can be compared. Adding a
policy version adds a column to the report and requires a snapshot update.

See [policy comparison](../agent/policy-comparison.md).
