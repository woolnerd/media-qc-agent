# Architectural Decision Records

This directory records decisions whose rationale and consequences should remain
visible after the implementation changes. The README describes current
behavior; ADRs explain how it got that way. They are not a task tracker.

Each ADR should state its status, context, decision, alternatives considered,
and consequences. Accepted records are not rewritten to hide later learning.
If a decision changes, add a new ADR that supersedes the old one.

## Index

- [`0001-constrain-model-actions-with-deterministic-policy.md`](0001-constrain-model-actions-with-deterministic-policy.md)
- [`0002-start-with-a-modular-monolith-and-sqlite.md`](0002-start-with-a-modular-monolith-and-sqlite.md)
- [`0003-use-synthetic-signals-before-raw-media-analysis.md`](0003-use-synthetic-signals-before-raw-media-analysis.md)
- [`0004-separate-clarification-from-repair-actions.md`](0004-separate-clarification-from-repair-actions.md)
- [`0005-separate-persistence-from-provider-execution.md`](0005-separate-persistence-from-provider-execution.md)
- [`0006-model-tts-input-as-a-derived-artifact.md`](0006-model-tts-input-as-a-derived-artifact.md)
- [`0007-deduplicate-provider-completions-transactionally.md`](0007-deduplicate-provider-completions-transactionally.md)
- [`0008-quarantine-stale-provider-completions.md`](0008-quarantine-stale-provider-completions.md)
- [`0009-record-immutable-artifact-lineage.md`](0009-record-immutable-artifact-lineage.md)
- [`0010-use-readable-demo-ids.md`](0010-use-readable-demo-ids.md)
- [`0011-repair-captions-without-video-provider.md`](0011-repair-captions-without-video-provider.md)
- [`0012-use-grounded-environment-metadata-before-render.md`](0012-use-grounded-environment-metadata-before-render.md)
- [`0013-use-synthetic-motion-jumps-for-video-quality.md`](0013-use-synthetic-motion-jumps-for-video-quality.md)
- [`0014-persist-artifact-bound-quality-evidence.md`](0014-persist-artifact-bound-quality-evidence.md)
- [`0015-bind-approval-to-immutable-plan-versions.md`](0015-bind-approval-to-immutable-plan-versions.md)
- [`0016-adapt-typed-classification-with-application-policy.md`](0016-adapt-typed-classification-with-application-policy.md)
- [`0017-use-luna-as-the-low-cost-chat-default.md`](0017-use-luna-as-the-low-cost-chat-default.md)
- [`0018-reserve-approved-plans-before-provider-submission.md`](0018-reserve-approved-plans-before-provider-submission.md)
- [`0019-organize-by-responsibility.md`](0019-organize-by-responsibility.md)
- [`0020-expose-local-review-api.md`](0020-expose-local-review-api.md)
- [`0021-lease-durable-provider-submissions.md`](0021-lease-durable-provider-submissions.md)
- [`0022-render-local-review-and-fault-controls.md`](0022-render-local-review-and-fault-controls.md)
- [`0023-observe-workflow-without-controlling-it.md`](0023-observe-workflow-without-controlling-it.md)
- [`0024-trace-model-turns-separately-from-evaluation-records.md`](0024-trace-model-turns-separately-from-evaluation-records.md)
- [`0025-compare-acceptance-policies-on-recorded-outputs.md`](0025-compare-acceptance-policies-on-recorded-outputs.md)
- [`0026-default-to-grounded-scope-acceptance.md`](0026-default-to-grounded-scope-acceptance.md)
- [`0027-keep-gate-precedence-and-scope-the-agent-to-gate-gaps.md`](0027-keep-gate-precedence-and-scope-the-agent-to-gate-gaps.md)
- [`0028-make-plan-versions-the-authority-for-current-plan-state.md`](0028-make-plan-versions-the-authority-for-current-plan-state.md)
