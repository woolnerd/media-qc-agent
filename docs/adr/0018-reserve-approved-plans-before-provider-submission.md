# 0018: Reserve approved plans before provider submission

Status: Accepted

Updates ADR 0005's store interface and supersedes ADR 0015's deferral of
protection against concurrent plan edits during provider submission.

## Context

Checking approval before calling the provider and checking the revision after
acceptance leaves a race. An intervening edit can invalidate approval while the
provider still creates a job. Local recording then fails, leaving that job
untracked. A crash followed by an edit can also replace the recovery key.

## Decision

Add `reserve_submission` to `WorkflowStore`. In a SQLite writer transaction,
recheck the caller's reviewed version and the exact current approval, then
persist `submitting` before calling the provider. Plan edits, replacement
binding, and new retries cannot proceed from this state.

Record provider acceptance only against that reserved revision. A crash or an
ambiguous provider error retains the reservation. Recovery resubmits the same
revision with the same idempotency key; the provider must deduplicate it.
Once acceptance is recorded, the run moves to `submitted` and existing
completion rules apply. Caption repair remains a local atomic transaction.

## Alternatives considered

- Recheck only after acceptance: detects stale attribution after the external
  side effect has already happened.
- Release the reservation on any exception: an uncertain acceptance could then
  lead to a new key and duplicate provider cost.
- Hold the SQLite write transaction across the network call: blocks unrelated
  writes and does not preserve the attempt after process failure.

## Consequences

An edit that wins before reservation prevents the provider call. A reservation
that wins prevents edits until acceptance is reconciled. The unresolved attempt
survives restart with its approved scope and recovery key intact.

This is not a distributed worker lease. Multiple workers can submit the same
reserved key, so provider idempotency is still required. Classified failures,
worker coordination, and an operator reconciliation surface remain planned.
