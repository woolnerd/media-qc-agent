# ADR 0028: Make plan versions the authority for current plan state

Status: Accepted. Builds on [ADR 0015](0015-bind-approval-to-immutable-plan-versions.md)
and replaces its legacy-upgrade rule.

## Context

The run row stored the current plan, sources, and recovery key, and every
change also appended an immutable plan version holding the same values.
Approval checks compared the two copies field by field. Four recovery-key
formats existed, and three were overwritten as soon as a second version was
written. Schema upgrades were applied in place on every startup.

## Decision

A run points at its current plan version. Every plan change (creation,
creative choice, edit, input binding, retry) appends one version and moves the
pointer in the same transaction. The run's plan, sources, and recovery key are
read from that version. The run row keeps only execution state and the sources
its finding was observed on. A version has a recovery key unless it still
awaits repair input, and the key is unique to the version.

Provider jobs reference their plan version instead of copying its sources and
action. The schema carries a version number. A database with another version
is rejected with instructions to delete it, instead of being migrated.

## Alternatives considered

- **Keep both copies and test that they agree.** This keeps every write in two
  places and makes approval depend on comparing them.
- **Store the plan only on the run row.** This loses the immutable history
  that approval binds to.
- **Write migrations.** The only persisted data is a local synthetic demo
  database, which the seed can recreate.

## Consequences

Approval reduces to: the current version is approved, and its targets still
match the run's active video and captions. Tampering with the run row cannot
change what a submission sends. Existing demo databases must be deleted once.
A production deployment would need real migrations before persisting data
that cannot be recreated.
