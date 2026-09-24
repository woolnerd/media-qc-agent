# ADR 0001: Constrain model actions with deterministic policy

Status: Accepted

## Context

Quality findings may require ambiguous language interpretation, but repair
execution can invalidate artifacts and launch expensive external jobs. Allowing
a model to choose and execute arbitrary repairs would make safety properties
difficult to test and audit.

## Decision

The model-facing layer may classify findings, cite evidence, identify
uncertainty, and propose repairs. Pure application policy maps supported failure
classes to allowed actions and invalidations. Stateful application code owns
approval validation, transitions, idempotency, and provider side effects.

## Alternatives considered

- Let the model plan and execute provider tools directly.
- Encode the complete diagnosis and workflow in deterministic rules.
- Require a human to manually operate every transition.

## Consequences

The boundary makes dangerous behavior explicit and testable, but limits the
agent to failure classes represented by application policy. Adding a new repair
capability requires both model-facing evaluation and deterministic policy work.
