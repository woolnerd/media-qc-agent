# ADR 0009: Record immutable artifact lineage from provider jobs

Status: Accepted

## Context

A successful provider callback previously marked a run succeeded without
recording the video it produced. A retry could therefore replace the active
result without preserving or identifying its predecessor's inputs. Caption
outputs also need a reference to the exact video used to make them.

## Decision

Store artifact versions under unique IDs and store each dependency as an
artifact ID plus its source role. A run starts with explicit script, TTS input,
avatar, and voice version IDs. A provider job snapshots those IDs at submission.
When the active job completes, one transaction records the event, creates a
video version with dependencies from that job, and promotes it as the run's
active video. Stale and duplicate completions create no video. A caption
version references one exact video version.

A script, TTS input, or avatar repair stays in `needs_repair_input` until a
replacement version of the expected kind is bound. Binding that version moves
the run to `awaiting_approval` and gives the submission a key tied to that
replacement. A later video retry keeps the previous version and active pointer
until the replacement succeeds.

## Consequences

Tests and synthetic callers must create source versions before opening a run.
Version rows and dependencies are append-only through the repository API. This
slice records version identities and lineage, not media bytes or caption
generation jobs. A future caption executor can use the exact video dependency
without changing the lineage model.
