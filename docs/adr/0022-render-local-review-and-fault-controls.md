# ADR 0022: Render local review and explicit synthetic fault controls

Status: Accepted

## Context

The local API and durable worker exist. Issue #18 needs a human review surface
that makes exact approvals, immutable lineage, and uncertain acceptance recovery
visible. A small demo benefits from directly demonstrating the failure boundary.

## Decision

Serve pure escaped HTML and CSS from FastAPI with native validated forms. Read
workflow data through a consistent snapshot. All commands carry the reviewed
plan version and delegate to guarded repository operations. Add optional
run/version filters to the existing worker claim so the UI steps only the
selected approved plan.

An explicit synthetic harness can interrupt after fake-provider acceptance,
recover after the real persisted lease expires, complete a fake job, and replay
its existing completion event. Share the durable provider ledger path with the
CLI worker. Never modify lease deadlines to make the demo faster.

This extends ADR 0020's request-only review boundary for explicitly named fake
worker controls. JSON approvals still only record READY; live provider execution
is not exposed through these forms.

## Alternatives

- A separate SPA: more tooling and duplicated client state for five local flows.
- JSON docs only: does not show lineage, approval history, or the one-version proof
  together in a human review surface.
- A second submission implementation: duplicates leasing and approval logic.
- Forced lease expiry: hides the real ownership constraint the demo should teach.

## Consequences

No JavaScript/build dependency is needed; multipart form parsing adds one pinned
runtime dependency. Rendering and form controls are independently testable.
The local API exposes explicit synthetic side effects, with same-origin and
allowed-host checks, and requires a loopback-only deployment. These are not an
authentication boundary. The independent ledger is observed outside the workflow
read transaction, so its displayed counters are an observation, not an atomic
cross-database snapshot. Manual refresh applies except during locked submissions.
