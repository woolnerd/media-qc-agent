# Architecture invariants

These are the guarantees a reviewer should understand before approving a change.
The implementation and regression tests must agree with them. A deliberate change
to a guarantee needs an ADR and an explicit explanation in the PR.

## Ownership and boundaries

```mermaid
flowchart TD
    Feedback[Feedback and artifact evidence] --> Model[Model provider]
    Model --> Validation[Strict output and policy validation]
    Validation --> Plan[Immutable plan version]
    Plan --> Human[Human clarification and approval]
    Human --> Store[Repository: atomic reservation]
    Store --> Executor[Executor: submit reserved key]
    Executor --> Provider[Media provider: idempotent submission]
    Provider --> Completion[Repository: guarded completion and lineage]
```

The model classifies supplied evidence. Pure application policy selects repair
scope. The repository owns durable transitions and artifact relationships.
The executor owns the external call. The provider must honor idempotency keys.
The model has no approval or media-generation authority.

## Guarantees and evidence

| ID | Guarantee | Boundary | Regression evidence |
| --- | --- | --- | --- |
| INV-01 | A model proposal cannot broaden the deterministic repair scope or select an unresolved creative choice. | Interpretation validation and pure planner | [Interpretation tests](../../tests/agent/test_interpretation.py), [planner tests](../../tests/domain/test_planner.py) |
| INV-02 | Approval identifies the exact immutable plan version, source versions, targets, and recovery key. An edit requires new approval. | Plan revisions, approval, and repository writes | [Plan-version tests](../../tests/workflow/test_plan_versions.py) |
| INV-03 | Provider submission starts only after an atomic reservation of the approved version. An edit that wins first prevents submission; a reservation that wins first prevents edits. | SQLite writer transaction before the external call | [Two-connection concurrency tests](../../tests/workflow/test_submission_concurrency.py), [executor tests](../../tests/workflow/test_executor.py) |
| INV-04 | An uncertain provider outcome retains the reserved version and key. Recovery cannot silently become a new attempt. | `submitting` state and provider contract | [Plan-version tests](../../tests/workflow/test_plan_versions.py), [crash recovery tests](../../tests/workflow/test_workflow.py) |
| INV-05 | Each generated artifact retains its exact input lineage. A stale or duplicate completion cannot promote an obsolete video or create a second version. | Provider-job snapshots and completion transaction | [Artifact tests](../../tests/workflow/test_artifact_lineage.py), [provider-event tests](../../tests/workflow/test_provider_events.py) |
| INV-06 | Caption-only repair preserves the accepted video and creates no video-provider job. | Local caption transaction and executor guard | [Caption tests](../../tests/quality/test_captions.py) |
| INV-07 | Accepted citations refer to supplied evidence records and artifact IDs. Facts, inferences, and uncertainty remain distinguishable. | Request and interpretation validation; evidence storage | [Interpretation tests](../../tests/agent/test_interpretation.py), [evidence tests](../../tests/domain/test_evidence.py) |

For each affected invariant, the PR should name a test and describe the failure
sequence it rules out. Test counts alone are insufficient evidence.

## Limits and tradeoffs

- SQLite serializes writers. The reservation commits before the network call,
  keeping network latency outside the write transaction.
- Reservation protects plan scope. It does not implement a worker lease or
  prevent two workers from submitting the same key. Provider deduplication is
  necessary for avoiding duplicate external jobs.
- A timeout can mean the provider accepted the job. Keeping `submitting` can
  delay edits, but preserves the recovery key until acceptance is reconciled.
- Structural validation and citation integrity do not establish that a model's
  diagnosis is semantically correct. Labeled live evaluations and human review
  address that separate question; offline fixture replay tests the plumbing.
- Quality signals and fixtures illustrate workflow behavior. They are not
  calibrated evidence of production speech or video quality.

See [exact plan approval](plan-versions.md), [submission decisions](../adr/0018-reserve-approved-plans-before-provider-submission.md),
and [quality gates](../development/quality-gates.md).
