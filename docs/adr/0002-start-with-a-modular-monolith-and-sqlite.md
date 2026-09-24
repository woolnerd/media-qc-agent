# ADR 0002: Start with a modular monolith and SQLite

Status: Accepted

## Context

The project needs to demonstrate durable state, crash recovery, callback
deduplication, and artifact lineage. It does not yet have scale evidence that
justifies distributed deployment or operationally heavy infrastructure.

## Decision

Keep the first implementation in one Python application with explicit module
boundaries. Use SQLite while proving workflow semantics and deterministic fake
providers for failure scenarios. Introduce Postgres, a workflow engine, or
separate services only when a concrete concurrency or operational requirement
cannot be met cleanly.

## Alternatives considered

- Begin with microservices and a message broker.
- Adopt a durable workflow framework before defining the domain transitions.
- Keep all state in memory for the initial demonstration.

## Consequences

The system remains inexpensive to run and easy to inspect. It must avoid
assuming that SQLite behavior represents all production concurrency semantics,
and it needs explicit evidence before claiming those semantics at larger scale.
