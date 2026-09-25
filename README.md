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
- [`docs/adr/`](docs/adr/) — decisions, alternatives, and consequences.
- [`PROJECT_STATE.md`](PROJECT_STATE.md) — the current handoff snapshot.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — development and verification workflow.
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
idempotent provider submission
```

It currently demonstrates:

- a pure repair policy for five failure classes, distinguishing TTS input
  compatibility from caption defects;
- a deterministic provider/model spoken-text gate that preserves authored text,
  normalizes unambiguous notation, and blocks unsafe TTS input versions (see
  [`docs/spoken-text-gate.md`](docs/spoken-text-gate.md));
- deterministic caption formatting and timing checks with evidence, plus a
  caption-only repair that keeps the accepted video (see
  [`docs/caption-quality.md`](docs/caption-quality.md));
- a synthetic script/avatar environment gate that grounds an oven/office
  mismatch in versioned metadata and asks a human to choose the repair (see
  [`docs/environment-compatibility.md`](docs/environment-compatibility.md));
- a distinction between unresolved creative input and executable approval;
- a persisted clarification request with separate script and avatar repair
  options, followed by a specific plan when a human selects one;
- durable SQLite workflow state;
- persisted provider jobs and deduplicated completion events;
- immutable source, video, and caption version identities with exact lineage;
- a stable idempotency key for external submission; and
- recovery from a crash after provider acceptance without creating a second
  paid job.

It does **not** yet inspect raw images or videos, call an LLM, expose a provider
callback API or UI, or generate caption content.
Those capabilities remain planned work, and the GitHub issues keep that
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

The code follows the same boundary: `workflow.py` defines shared run state,
`repository.py` owns SQLite schema and transitions, and `executor.py` submits
approved work through a small store interface and the provider adapter.

## Run the tests

No third-party packages are required for the current slice.

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

GitHub Actions runs the test suite, Ruff, mypy, compilation, and the synthetic
demo on every pull request and push to `main`. The workflow is described in
[`CONTRIBUTING.md`](CONTRIBUTING.md).

The test suite includes the uncertain-outcome case where a provider accepts a
job and the process crashes before saving the provider job ID. On restart, the
executor retries with the same idempotency key and receives the original job
instead of creating a duplicate.

## Run the synthetic demo

From the repository root:

```bash
PYTHONPATH=src python3 -m media_qc_agent.demo
```

The output shows a stale old-job callback, the applied current-job callback,
the active video version and its exact input versions, and a caption tied to
that video. The demo runs in memory with a fake provider; there is no UI or
external service to start in this milestone. See
[`docs/provider-transitions.md`](docs/provider-transitions.md) for the callback
rules it exercises.
