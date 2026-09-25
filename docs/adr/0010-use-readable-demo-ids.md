# ADR 0010: Use readable, namespaced IDs in the demo

Status: Accepted

## Context

SQLite stores IDs as `TEXT`, but storage type does not define an ID format.
The early fixtures use readable strings. Artifact IDs share one table, so
their kind must be clear and collisions between kinds must be avoided.

## Decision

- Callers choose run IDs as lowercase hyphenated slugs, such as `demo-run`.
- Callers choose source and caption version IDs with a kind prefix and a
  lowercase slug suffix: `script-`, `tts-`, `avatar-`, `voice-`, or `caption-`.
- Provider job and event IDs remain opaque external strings. We require only
  that they are not blank; we preserve their exact value.
- The workflow derives one video version ID from each accepted provider job as
  `video:<external-job-id>`. The provider job ID is also stored separately, so
  no code needs to parse the video ID to recover lineage.
- Workflow idempotency keys remain internal strings built from the validated
  run ID, action, and, for later attempts, the replacement ID or retry number.

The repository validates caller IDs at creation and external IDs when it
records submissions and completions. Database primary keys and foreign keys
enforce uniqueness and references.

## Alternatives considered

- UUIDs for every local ID would give a familiar, scalable format but make
  synthetic traces harder to read at this stage.
- Unrestricted caller strings would keep fixture setup simple but allow
  ambiguous artifact identities in the shared version table.

## Consequences

The demo remains easy to inspect without UUID tooling. Source and caption IDs
cannot collide with generated video IDs because they use disjoint prefixes.
These readable IDs are a demo contract, not a claim that provider IDs follow
our naming scheme. A production adapter could use UUIDs for local IDs without
changing the `TEXT` storage type or lineage relationships.
