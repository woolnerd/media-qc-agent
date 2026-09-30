# 0021: Lease durable provider submissions and preserve unknown outcomes

Status: Accepted

## Context

Approved API work needs execution outside request handlers, recovery across
process restarts, and bounded provider pressure. Submission reservation locks
the plan; alone it does not coordinate workers or bound automatic retries.

## Decision

Use SQLite to atomically choose an approved plan, reserve its submission, and
record a lease/attempt. Fence reservation and job recording by the exact run,
plan, owner, and attempt. Persist one shared worker policy. Count outstanding
provider jobs and uncertain reservations against capacity, including superseded
jobs awaiting completion. Recovery preserves the original plan/key and budget.

Keep network calls outside SQLite transactions. A fixed lease can expire during
a call, so retain the provider idempotency requirement. Timeout/connection errors
back off; contract errors or attempt exhaustion stop recovery and retain the
uncertain reservation. Human retries for known jobs remain separate approved
plan versions. Caption repair stays local.

Move connection assembly into `workflow/database.py` so API and worker can share
it without workflow depending on HTTP. Give the fake provider its own persistent
ledger, simulating externally owned state across actual interpreter restarts.

## Alternatives considered

- Poll without leases: competing workers can repeatedly call the provider and
  stale workers can overwrite recovery bookkeeping.
- Hold a SQLite write transaction during submission: blocks approval/callbacks
  behind provider latency and still cannot make remote acceptance atomic.
- Redis/Celery or a distributed scheduler: adds operational dependencies beyond
  the local demonstration before SQLite's limits have been exercised.
- Reset after exhaustion: may create another paid job while acceptance is unknown.

## Consequences

New queue/policy tables record durable attempts. The repository retains approval,
state, and lineage authority; the worker composes its executor with lease guards.
Provider callbacks release outstanding capacity. Slow overlapping calls can
occur after expiry; job deduplication still depends on the provider. Clock
consistency, provider timeouts, operational reconciliation, and a distributed
scheduler remain deployment concerns. Bounds trade availability for avoiding
unapproved duplicate work.
