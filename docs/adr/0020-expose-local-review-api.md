# 0020: Expose local review through existing workflow commands

Status: Accepted

## Context

A review surface needs scenarios, evidence, immutable plans, exact approvals,
and callbacks. Duplicating state transitions in HTTP handlers would introduce
competing approval and lineage guarantees.

## Decision

Add an `api` package with request schemas, scenario assembly, routers, and an
application factory. Keep policy and durable transitions in the repository.
Synchronous handlers use a fresh SQLite connection in their own thread and
enable foreign keys on each connection. Seed a dedicated file database during
lifespan startup using one server process; importing the factory performs no I/O.

Approval records `ready`. The executor and planned worker own external submission.
Caption-only completion and callbacks use existing guarded repository commands.
Run IDs remain caller-chosen readable slugs. Pin FastAPI, Starlette, Pydantic,
Uvicorn, and the test client dependency; serve the synthetic demo on loopback.

## Alternatives considered

- Submit in the approval handler: couples requests to uncertain external
  outcomes and bypasses the planned durable worker.
- Async ORM or shared connection: introduces a persistence rewrite or thread
  coordination before either is needed.
- Arbitrary findings/uploads: expands beyond scenario-based review in this issue.

## Consequences

Runtime dependencies are added. Existing transitions keep ownership and HTTP
tests challenge their guards. SQLite serializes concurrent writes. Fixture IDs
are reserved; multiple server processes sharing startup seeding are unsupported.
Authentication, callback signatures, real media, the worker, and UI remain future work.
