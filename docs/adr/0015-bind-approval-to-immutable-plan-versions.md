# 0015: Bind approval to immutable plan versions

Status: Accepted

Submission behavior updated by [ADR 0018](0018-reserve-approved-plans-before-provider-submission.md).
The original gap described below is now protected by a durable `submitting`
reservation; worker leases remain planned work.
[ADR 0028](0028-make-plan-versions-the-authority-for-current-plan-state.md) makes
the current snapshot the only copy of current plan state and replaces the
legacy-upgrade rule below with a schema version check.

A mutable plan plus a READY status does not establish which inputs a human
reviewed. A stale approval could otherwise authorize a later edit or replacement.

Store append-only snapshots for every concrete plan, branch selection,
replacement binding, edit, and retry. Store approval as a reference to one exact
snapshot and require the caller's reviewed ID. Snapshot the complete source
lineage, target artifacts, original finding artifact, replacement choices and
idempotency key. Keep repair-policy validation in pure code.

Reject stale approval/edit requests under SQLite writer transactions. Require a
matching approval before provider submission or caption repair, and check the
submitted revision again when recording provider acceptance. Preserve historical
approvals while making an edited revision await a new decision. Legacy READY
runs require a fresh version approval on upgrade.

A single mutable approval flag was rejected because it loses review scope.
Overwriting old snapshots was rejected because it loses the audit trail. Plans
cannot grant broader regeneration authority than the deterministic policy.

The executor still has a remote-side-effect gap. Revision checks prevent an
accepted job from being silently attributed to an edited plan, but distributed
leases and reconciliation of concurrent edits belong to the durable worker.
