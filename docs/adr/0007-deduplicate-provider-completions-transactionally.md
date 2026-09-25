# ADR 0007: Deduplicate provider completions transactionally

Status: Accepted

## Context

A provider may deliver the same completion callback more than once. The
submission idempotency key prevents duplicate paid jobs, but it does not stop
repeated callbacks from advancing workflow state twice or creating duplicate
outputs later.

## Decision

Persist provider jobs and completion events in SQLite with unique external job
and event identifiers. Record a job in the same transaction that marks its run
submitted. Record an event in the same transaction that changes a submitted run
to succeeded. On redelivery of an existing event ID for the same job, return
the existing result without writing again; reject reuse of that event ID for
another job. A second completion ID for an already-succeeded job is retained
for audit but does not repeat the transition.

The job table allows multiple jobs per run so a later retry can retain older
jobs. Only one active job is modeled by the current `WorkflowRun`; selecting
which job may promote an artifact is separate stale-event work.

## Alternatives considered

- Deduplicate in memory. This would forget prior events after a restart.
- Use the run's succeeded status alone. This would not identify an event ID
  reused for a different job or retain each delivery for audit.
- Make `run_id` unique in the job table. That would prevent future retries from
  retaining their earlier job records.

## Consequences

Duplicate delivery is harmless across repository restarts, and event/job
identity can be inspected. This slice records completion state but does not yet
create a versioned media artifact. When artifact versions are added, promotion
must share the event transaction. Stale and out-of-order callbacks remain a
separate transition rule to implement before retries are active.
