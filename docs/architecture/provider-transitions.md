# Provider completion transitions

## Submission boundary

Video-provider work follows `ready → submitting → submitted → succeeded`.
`reserve_submission` atomically verifies the reviewed plan version and current
approval, then commits `submitting` before the provider call. That state blocks
plan edits, input rebinding, and new retries. `record_submission` requires the
reserved version and records the accepted job before moving to `submitted`.

A crash or ambiguous provider error keeps the run `submitting`. Resume the
same attempt with the same idempotency key, which the provider must honor for
deduplication. The reservation is durable plan protection; distributed worker
leases remain planned work. See [exact plan approval](plan-versions.md) and
[ADR 0018](../adr/0018-reserve-approved-plans-before-provider-submission.md).

## Completion handling

Each submitted provider job snapshots the script, TTS input, avatar, and voice
version IDs. The run holds the current job ID and active video version ID.
Callback handling takes a SQLite write lock, records the event, and applies
the transition below in one transaction.

| Callback | Recorded disposition | Run or artifact effect |
| --- | --- | --- |
| First completion for the active submitted job | `applied` | Create one video version from that job's saved inputs; mark the run succeeded and point to the video. |
| Same event ID delivered again | Original disposition | No new write or transition. |
| Different completion ID for the active succeeded job | `redundant` | Keep the event for audit; create no second video. |
| Completion for a superseded job | `stale` | Keep the event for audit; do not change the active video or run. |
| Completion for an active job in any other run state | `rejected` with reason | Keep the event for audit; do not create a video or advance the run. |
| Event ID reused for a different job | Error | Roll back; the event ID cannot change owners. |

Requesting a retry from `submitted` or `succeeded` clears the current job ID,
retains any active video version, and returns to `awaiting_approval` with a new
idempotency key. A stale callback can arrive before or after the replacement
job completes; neither order can promote its video. A replacement video becomes
active only when its own job completes. Earlier video and caption versions
remain readable, with each caption pointing to its exact source video.

The decisions behind these rules are recorded in
[ADR 0007](../adr/0007-deduplicate-provider-completions-transactionally.md),
[ADR 0008](../adr/0008-quarantine-stale-provider-completions.md), and
[ADR 0009](../adr/0009-record-immutable-artifact-lineage.md).
