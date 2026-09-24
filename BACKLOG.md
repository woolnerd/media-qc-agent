# Working Backlog

This is the temporary source of truth for implementation work. It exists to
keep volatile tasks out of `PROJECT_PLAN.md`. Once GitHub issues and milestones
are established, migrate every remaining item there and replace this file with
a link to that system of record.

## Current milestone: versioned artifacts and provider events

### 0. Correct the TTS-input compatibility failure model

The current executable policy models the `450*F` example as a caption-only
failure. The actual failure was a provider/model-specific text-to-speech input
incompatibility: the voice pronounced the notation as “four hundred fifty star
F” rather than “four hundred fifty degrees Fahrenheit.” Correct this mismatch
before extending the domain model.

Acceptance criteria:

- A failing test captures the corrected failure and minimum-repair behavior.
- The failure taxonomy no longer presents this case as `CAPTION_FORMAT` or
  assumes one normalization rule works for every TTS provider and model.
- The repair preserves authored intent, avatar, and voice profile while
  invalidating any output generated from unsafe provider-facing text.
- Decide whether normalized spoken text is a separate immutable artifact or a
  new script version, and where provider/model capability rules belong. Record
  the rationale in an ADR.

### 1. Deduplicate provider completion events

Acceptance criteria:

- A failing test first demonstrates processing the same provider event twice.
- `ProviderJob` and `ProviderEvent` records use unique external identifiers.
- Reprocessing an event returns the original result without a second state
  transition or artifact version.
- The event remains auditable.

### 2. Reject stale completions

Acceptance criteria:

- A failing test first demonstrates an older job completing after a newer retry
  exists.
- The old completion is recorded but cannot become the active video version.
- Impossible transitions are rejected or quarantined with an explicit reason.

### 3. Add versioned video and caption artifacts

Acceptance criteria:

- Video and caption versions are immutable.
- Each output records its exact source dependencies.
- A replacement video does not overwrite prior versions.
- Caption lineage identifies the video version from which it was produced.
- A selected script or avatar repair can bind an exact replacement artifact
  version before entering `awaiting_approval`.

### 4. Document the resulting transition rules

Acceptance criteria:

- Tests cover duplicate and out-of-order callbacks.
- The full test suite, formatting, linting, and type checks pass.
- Any material design choice is captured in `docs/adr/`.

## Later milestones

### Deterministic quality gates

- Normalize and validate provider-facing spoken text against explicit
  provider/model capabilities, including symbols, temperatures, and units.
- Add caption validation as a separate quality concern.
- Add synthetic script/background compatibility fixtures.
- Define a deterministic visual-quality signal for jerky-video scenarios.
- Specify the evidence and threshold required to create a `VISUAL_QUALITY`
  finding, including when a reviewer must make the diagnosis. A finding should
  cite the observed artifact and should not launch a retry before approval.
- Store findings and evidence against exact artifact versions.

### Agent interpretation and repair planning

- Define strict structured output for feedback classification.
- Add a model-provider interface and deterministic fake.
- Validate proposals against the deterministic repair policy.
- Extend persisted clarification requests with exact artifact choices and
  versioned repair-plan approval. The initial script/avatar branch selection is
  implemented, but artifact binding and plan versioning are not.
- Create the initial synthetic evaluation dataset.

### API, worker, and review surface

- Add API endpoints for scenarios, findings, plans, approvals, and callbacks.
- Move execution into a durable worker.
- Build a small review surface with fault-injection controls.

### Observability and evaluation

- Add structured logs, metrics, and OpenTelemetry-compatible spans.
- Add model traces and evaluation scores.
- Create an evaluation command suitable for CI.
- Compare at least two prompt or policy versions.

### Presentation finish

- Replace placeholders with polished synthetic assets.
- Add an architecture diagram and short demonstration recording.
- Document limitations and rejected alternatives.
- Complete security, code, test, and UX reviews.
