# ADR 0008: Quarantine stale provider completions

Status: Accepted

## Context

A retry can supersede a submitted provider job before that job's completion
arrives. The callback still matters for audit, but it must not advance the run
or replace work from the current job.

## Decision

A retry request clears the run's active job, generates a new idempotency key,
and returns the plan to approval. Each accepted submission remains in the job
table. A completion is applied only when its job matches the run's active job
and the run is submitted. Other completions are stored with a disposition and
explicit reason: stale for a superseded job, redundant for another completion
of the active successful job, or rejected for an impossible active-job state.
The event record and any workflow transition share one transaction.

## Consequences

Retries require fresh approval and use distinct provider idempotency keys. A
late callback cannot make a run succeed while a newer retry is awaiting
approval or submitted. This slice does not yet create video artifact versions;
promotion of a version must later use the same active-job check and transaction.
