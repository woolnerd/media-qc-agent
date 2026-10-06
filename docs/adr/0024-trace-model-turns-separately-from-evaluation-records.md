# ADR 0024: Trace model turns separately from evaluation records

## Context

Saved evaluation results reported pass or fail without the prompt that produced
them, the raw model output, or whether validation rejected the output or
accepted a wrong diagnosis. Workflow telemetry forbids review text and evidence,
while model debugging needs those inputs and outputs.

## Decision

Give each model provider an identity with a content-hashed prompt version. Trace
each interpretation turn with OpenTelemetry using the same allowlisted, hashed
attribute rules as workflow spans, separating the model call from validation and
planning. Record full synthetic inputs, outputs, and scores only in evaluator
JSONL records, joined to spans by trace ID. Link turns to runs through the shared
run reference. Move that reference helper into the domain so agent and workflow
spans hash identically without the agent importing workflow code.

## Alternatives rejected

- Manual prompt version labels: an edited prompt could keep a stale label.
- Prompt or output content in span attributes or events: violates ADR 0023 and
  would export review text wherever an exporter sends spans.
- Span parent links from diagnosis to run: the run does not exist until after
  diagnosis, so a shared reference is the accurate relationship.
- New score labels for rejection versus provider error: would make new runs
  incomparable with saved results; the record's outcome carries the distinction.

## Consequences

Each provider must declare its identity. Prompt versions change with any policy
change embedded in the chat prompt. Records are suitable only for synthetic data.
Trace IDs are absent unless an SDK is injected. Comparing versions and running
evaluations as a CI gate remain separate work.

See [model traces](../agent/model-traces.md).
