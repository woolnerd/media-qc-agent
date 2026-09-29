# Media QC Agent

AI-media pipelines often discover defects only after their most expensive
artifact has been generated. Media QC Agent explores how to catch those
failures earlier, preserve valid upstream work, and safely coordinate
human-approved repairs across asynchronous provider jobs.

This is a focused, clean-room implementation built with synthetic data. It
investigates the engineering tradeoffs behind versioned media workflows,
model-assisted diagnosis, durable execution, and idempotent recovery; it does
not reproduce a former client product or claim production-scale readiness.

## Start here

- [`PROJECT_PLAN.md`](PROJECT_PLAN.md) — the stable product and technical
  thesis: what the system should do and why.
- [GitHub issues](https://github.com/woolnerd/media-qc-agent/issues) — active
  implementation tasks and acceptance criteria, grouped by milestones.
- [`docs/adr/`](docs/adr) — decisions, alternatives, and consequences.
- [`PROJECT_STATE.md`](PROJECT_STATE.md) — the current handoff snapshot.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — development and verification workflow.
- [Architecture invariants](docs/architecture/architecture-invariants.md) — guarantees,
  ownership boundaries, and regression evidence.
- [Quality gates](docs/development/quality-gates.md) — architecture review, failure scenarios,
  and adversarial review before merging.
- [`docs/adr/0010-use-readable-demo-ids.md`](docs/adr/0010-use-readable-demo-ids.md)
  — the ID format and ownership rules used by this demo.

## Current executable slice

The first slice establishes the deterministic safety boundary that a future
agent must obey after a quality finding is classified:

```text
structured quality finding
          ↓
minimum-repair policy
          ↓
durable workflow record
          ↓
human approval
          ↓
durable submission reservation (submitting)
          ↓
idempotent provider submission
```

The TTS notation and visual-motion checks are demo-grade fixtures. Their
profiles, samples, thresholds, and visual confidence value are illustrative;
they have not been validated against real provider speech or human-rated video.
A pass means only that the local rule did not fire.

It currently demonstrates:

- fifteen saved interpretation evaluation cases, with clear, ambiguous, and
  adversarial examples for every failure class (see [`evals/`](evals/README.md));
- a pure repair policy for five failure classes, distinguishing TTS input
  compatibility from caption defects;
- a deterministic provider/model spoken-text gate that preserves authored text,
  normalizes unambiguous notation, and blocks unsafe TTS input versions (see
  [`docs/quality/spoken-text-gate.md`](docs/quality/spoken-text-gate.md));
- deterministic caption formatting and timing checks with evidence, plus a
  caption-only repair that keeps the accepted video (see
  [`docs/quality/caption-quality.md`](docs/quality/caption-quality.md));
- a synthetic script/avatar environment gate that grounds an oven/office
  mismatch in versioned metadata and asks a human to choose the repair (see
  [`docs/quality/environment-compatibility.md`](docs/quality/environment-compatibility.md));
- a synthetic same-shot motion-jump signal tied to an exact video version,
  with human approval before a video retry (see
  [`docs/quality/visual-quality-signal.md`](docs/quality/visual-quality-signal.md));
- persisted findings and evidence tied to exact artifact versions, with facts,
  inferences, and uncertainty labeled separately (see
  [`docs/quality/quality-evidence.md`](docs/quality/quality-evidence.md));
- immutable plan revisions and approval bound to exact artifact choices (see
  [`docs/architecture/plan-versions.md`](docs/architecture/plan-versions.md));
- a distinction between unresolved creative input and executable approval;
- a persisted clarification request with separate script and avatar repair
  options, followed by a specific plan when a human selects one;
- durable SQLite workflow state;
- an atomic `ready → submitting` reservation that locks the approved plan before
  calling the provider;
- persisted provider jobs and deduplicated completion events;
- immutable source, video, and caption version identities with exact lineage;
- a stable idempotency key for external submission; and
- recovery from a crash after provider acceptance without creating a second
  paid job.

It does **not** yet inspect raw images or videos, expose a provider
callback API or UI, or generate caption content.
An OpenRouter adapter can interpret text feedback with a low-cost model;
see [`docs/agent/agent-interpretation.md`](docs/agent/agent-interpretation.md) for the explicit
live demo and its strict validation boundary.
An optional typed Jev classifier and matched cost/latency comparison are
documented in [`docs/agent/jev-classification.md`](docs/agent/jev-classification.md).
Those remaining capabilities are planned work, and the GitHub issues keep that
distinction explicit.

## Why the boundary matters

The model-facing layer may eventually interpret ambiguous feedback and evaluate
artifacts, but it will not receive unrestricted authority over regeneration.
Deterministic application code decides which transitions are valid, which
artifacts are invalidated, whether approval is current, and whether an external
side effect is safe to execute.

For example, jerky visual output should invalidate the video and its derived
captions while preserving the approved script, avatar, and voice. A bad script
has a wider invalidation boundary because its descendants no longer represent
approved input.

## Repository layout

```text
src/media_qc_agent/
  domain/       Artifact types, evidence, IDs, and pure repair policy
  quality/      Caption, environment, spoken-text, and visual checks
  workflow/     Run models, plan versions, SQLite persistence, execution
  agent/        Interpretation contracts, validation, and model adapters
  cli/          Offline demos and evaluation commands
tests/          Mirrors the five source packages
docs/
  architecture/ Guarantees, approval versions, and provider transitions
  quality/      Check behavior, evidence, and demo limitations
  agent/        Model interpretation and comparison methods
  development/  Quality gates and review procedure
  adr/          Decision history
evals/          Synthetic cases and recorded experiment results
```

The package root provides the public convenience imports. Implementation imports
use the responsibility packages directly. Start with [the documentation guide](docs/README.md)
for a reading order and [ADR 0019](docs/adr/0019-organize-by-responsibility.md)
for the organization rules and tradeoffs.

## Run the tests

No third-party packages are required for the current slice.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -t . -v
```

GitHub Actions runs the test suite, Ruff, mypy, compilation, and the synthetic
demo on every pull request and push to `main`. The workflow is described in
[`CONTRIBUTING.md`](CONTRIBUTING.md).

The provider workflow progresses through `awaiting_approval → ready → submitting
→ submitted → succeeded`. Before the external call, a SQLite writer transaction
rechecks approval and reserves the exact plan as `submitting`. Edits and input
rebinding are blocked while that submission is unresolved. Caption repair uses
its own local transaction and goes directly from `ready` to `succeeded`.

The test suite includes the uncertain-outcome case where a provider accepts a
job and the process crashes before saving the provider job ID. The run remains
`submitting`. On restart, the executor retries with the same idempotency key and
receives the original job instead of creating a duplicate. This relies on the
provider honoring that key; see [exact plan approval](docs/architecture/plan-versions.md).

## Run the synthetic demo

From the repository root:

```bash
PYTHONPATH=src python3 -m media_qc_agent.cli.demo
```

The output shows a stale old-job callback, the applied current-job callback,
the active video version and its exact input versions, and a caption tied to
that video. The demo runs in memory with a fake provider; there is no UI or
external service to start in this milestone. See
[`docs/architecture/provider-transitions.md`](docs/architecture/provider-transitions.md) for the callback
rules it exercises.
