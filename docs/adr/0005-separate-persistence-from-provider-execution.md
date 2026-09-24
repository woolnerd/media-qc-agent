# ADR 0005: Separate persistence from provider execution

Status: Accepted

## Context

The first slice placed workflow state, SQLite queries, and provider submission
in one module. That made it harder to read which rules protect durable state
and which code handles the external side effect.

## Decision

Keep `WorkflowRun` and `WorkflowStatus` in `workflow.py`. Put SQLite schema,
serialization, and guarded state updates in `repository.py`. Put provider
submission and crash-gap behavior in `executor.py`. The executor depends on a
small `WorkflowStore` protocol with `get` and `record_submission`; the SQLite
repository satisfies that protocol without an executor import.

## Alternatives considered

- Keep one module while separating the two classes internally.
- Introduce a general service or unit-of-work framework before the domain
  needs it.

## Consequences

Provider execution can be tested with an in-memory store. SQLite remains the
durable implementation and retains atomic transition guards. Future changes to
the database layout should not require changing provider execution unless the
store contract itself changes.
