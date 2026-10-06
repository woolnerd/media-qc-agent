# Attribute new rendering attempts

## Decision and implementation status

Allow users to intentionally supersede submitted or completed video jobs.
Persist who requested each new attempt and why, so accounting can distinguish
user-directed rendering from unnecessary agent work.

This is an agreed architecture requirement, **not yet implemented** in the
retry API or database. Plan rationale and callback reasons describe different
decisions and must not substitute for attempt attribution.

## Durable record

Create an immutable attempt-request record in the workflow database, atomically
with its new plan version. Link it to the run, new plan version, and superseded
provider job when applicable. Save the request time and these fields:

| Field | Meaning |
| --- | --- |
| `initiator` | `user`, `agent`, or `system`, established by a trusted application entry point |
| `reason` | Structured reason such as `user_override`, `quality_rejection`, or `provider_failure` |
| Evidence reference | Supporting finding or failure record, when applicable |

Initiator identifies who requested the work, separately from who approved the
plan or executed it. Agent and system requests retain exact-plan human approval
before rendering. Clients must not be able to relabel agent requests as user
overrides through a supplied field.

Include initial attempts when implementing attribution. Historical missing
attribution stays unknown. Worker recovery reuses the existing attempt and key;
it must not create a new request or change its attribution.

## Product behavior

An explicit request for another version may supersede an in-flight job. Explain
that the earlier render may still finish and consume credits. Superseding does
not imply provider cancellation or a refund. Earlier completions remain auditable
and cannot replace the newer attempt.

## Accounting and evaluation

Count distinct provider jobs and completion reports separately from callback
deliveries and applied replacements. Duplicate notifications do not add generated
jobs. Completion reports are not billing records; charges remain unknown unless
the provider supplies them.

Both user overrides and agent retries contribute to total resource consumption.
Report user-directed extra rendering separately in agent efficiency evaluation.
A stale completion alone does not prove an agent error. Attribution and evidence
are needed; a reason label alone also does not prove a retry was justified.

Attribution belongs in the database. Observability may emit safe identifiers and
structured categories after commit, without copying sensitive evidence or user
text into logs.

## Implementation verification

Test atomic request/plan creation, immutable attribution through recovery,
trusted initiator assignment, preservation through stale completions, and
separate accounting for user overrides and agent-requested rendering.
