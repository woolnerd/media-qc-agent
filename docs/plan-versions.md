# Exact plan approval

A repair plan is an immutable, numbered snapshot. It includes the action,
invalidations, input requirement, rationale, script/TTS/avatar/voice versions,
current video/caption targets, original observed artifact, exact replacement
choices, and provider idempotency key. `WorkflowRun.plan_version` is the current
snapshot; `get_plan_versions(run_id)` returns its history.

A human reviewer must send the ID they actually reviewed:

```python
reviewed = repository.get(run_id)
# Display reviewed.plan_version and its exact artifact choices to the human.
repository.approve(run_id, plan_version_id=reviewed.plan_version_id)
```

The caller must retain that ID across the human decision. Fetching the newest ID
only when an old review is approved would defeat stale-review detection.

`select_repair` selects an offered clarification branch. `bind_replacement`
records the exact new script, TTS input, or avatar ID; script changes also need
`bind_tts_input` for a validated matching derived version. Each operation appends
an immutable snapshot. Branch choice alone cannot authorize regeneration.

`revise_plan(..., expected_plan_version_id=...)` validates the edit against the
finding's minimum-repair policy and rejects stale editors. Edits and replacement
rebinding invalidate prior approval; the history still shows which old snapshot
was approved. Submitted work cannot be edited; `request_retry` creates a new
revision and requires its own approval. Each executable revision has a distinct
idempotency key; restarting the same revision retains its key.

Approval, plan writes, and submission reservations use SQLite writer
transactions. Before calling the provider, the executor atomically rechecks the
reviewed revision and approval against the exact plan, inputs, targets, and key,
then persists `submitting`. That reservation blocks edits, replacement rebinding,
and new retries until the accepted job is recorded. An edit that wins the writer
transaction first causes reservation to fail before any provider call.

A crash or ambiguous provider error leaves the workflow `submitting`. Restarting
resubmits the reserved revision using the same idempotency key, so an accepted
job can be recovered without creating a second job. Recording acceptance requires
the reservation and revision ID; the provider job stores that reference. Caption
repair checks approval within its own writer transaction. A completion still
passes the existing stale-job and immutable-lineage rules.

Database triggers prevent updates/deletes of plan versions and approvals. When
opening an existing database, initialization snapshots previously unversioned
plans and returns legacy READY runs to awaiting approval. It never invents an
approval for an old status. Already-submitted legacy jobs continue through their
existing completion rules; their historical plan reference remains unknown.

This reservation does not add a distributed execution lease. Concurrent workers
can call the provider with the same reserved key; the provider must honor that
key for deduplication. An ambiguous outcome must be reconciled with that key,
rather than releasing the reservation and approving a new attempt. Durable
worker coordination remains milestone 4 work.
