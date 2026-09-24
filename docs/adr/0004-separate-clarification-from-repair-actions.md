# ADR 0004: Separate clarification from repair actions

Status: Accepted

## Context

The initial policy represented a script/environment mismatch with
`CHOOSE_SCRIPT_OR_AVATAR`, a value of `RepairAction`. That value described a
question for a human rather than a repair the workflow could perform. It also
hid the different invalidation scopes of revising a script and changing an
avatar.

## Decision

An unresolved mismatch produces a persisted `ClarificationRequest` with two
`RepairOption` values. Each option names a specific `RepairAction` and its
invalidation scope. A human selects an offered action, creating a `RepairPlan`
in `needs_repair_input`. A replacement artifact version must be supplied before
approval. Neither the unresolved request nor the selected plan has an
idempotency key, and neither can be submitted.

## Alternatives considered

- Rename the old action to make its request semantics clearer.
- Keep one repair plan with an unresolved action and an empty invalidation set.
- Let the agent choose the script or avatar branch without human input.

## Consequences

The workflow now distinguishes a question from a chosen repair and records the
human's direction. The current slice still uses a fake provider and has no
versioned replacement inputs, so a selected branch cannot advance to approval
yet. Binding exact artifact versions is the next implementation step for this
path.
