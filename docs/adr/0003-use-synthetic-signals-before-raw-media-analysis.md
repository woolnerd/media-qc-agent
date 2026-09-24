# ADR 0003: Use synthetic signals before raw media analysis

Status: Accepted

## Context

The system ultimately needs evidence from scripts, captions, metadata, review
feedback, and media artifacts. General image and video quality evaluation would
introduce multimodal-model selection, media preprocessing, cost, latency, and a
new evaluation problem before the workflow semantics are established.

## Decision

Use synthetic artifacts, metadata, deterministic validators, and derived visual
signals for the first executable scenarios. Add direct image or video analysis
only for a named failure class with a measurable evaluation rubric. Continue to
model the underlying media artifact and its version even when a fixture stands
in for analysis.

## Alternatives considered

- Start with a general-purpose multimodal quality evaluator.
- Accept only human-written issue descriptions and never inspect artifacts.
- Delay all quality evaluation until the workflow engine is complete.

## Consequences

Early tests can focus on repair correctness and distributed-systems behavior,
but the demo must label simulated findings honestly. The eventual media
evaluator will require its own dataset, false-pass analysis, and cost/latency
tradeoff assessment.
