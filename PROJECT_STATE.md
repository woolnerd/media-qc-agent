# Project State

Last updated: 2026-09-24
Repository: `/Users/davewoolner/Code/Learning/media-qc-agent`
Current branch: `codex/initial-media-qc` (PR #1)
State confidence: High

## Purpose

Media QC Agent is a clean-room, synthetic-data implementation of a quality
supervisor for asynchronous AI-media production. It explores whether
model-assisted diagnosis can be safely constrained by deterministic repair,
approval, lineage, and execution policies so defects are caught earlier and
valid upstream work is preserved.

## Current Working State

The initial vertical slice is executable. It contains immutable domain types, a
pure minimum-repair policy for four failure classes, SQLite-backed workflow
state, an idempotent fake video provider, approval gating, and recovery from a
simulated crash after provider acceptance but before local persistence.
Environment mismatches now persist a clarification request with separate
script and avatar choices. Selecting one creates a specific repair plan in
`needs_repair_input`; approval and submission remain blocked until a replacement
artifact version can be bound to it.

On 2026-09-24, all 17 standard-library unit tests passed. The initial project
is under review in PR #1.

## Active Work

Documentation has been separated by purpose: `PROJECT_PLAN.md` holds the stable
product and technical thesis, `BACKLOG.md` is the temporary working task list,
`docs/adr/` records decisions, and `CONTRIBUTING.md` records repository
workflow. GitHub issues have not yet replaced the temporary backlog.
The initial feature branch is open for review in PR #1.

No Phase 1 implementation work is confirmed to have started. A domain mismatch
was identified during documentation review: the implemented caption-format
case represents a failure that should instead be modeled as provider/model-
specific TTS-input incompatibility.

## Key Decisions

- Models may diagnose and propose; deterministic code controls invalidation,
  approvals, transitions, idempotency, and provider side effects.
- Start as a modular Python monolith with SQLite and deterministic provider
  fakes. Add infrastructure only in response to demonstrated requirements.
- Use synthetic evidence and derived visual signals before introducing direct
  image or video analysis.
- Treat text normalization as provider/model-specific compatibility rather than
  assuming universal pronunciation rules.
- Preserve immutable artifact versions and exact dependency lineage rather than
  overwriting outputs.
- Keep all client data, code, prompts, credentials, and private assets outside
  this clean-room implementation.

## Evidence and User Learning

The failure taxonomy is informed by firsthand workflow experience: late script
review, script/environment mismatch, voice generation that pronounced notation
literally, and jerky generated video. There is no external customer validation,
usage evidence, or market research for this implementation; it is a focused
engineering project rather than a validated commercial product.

## Blockers and Risks

- Provider callback semantics and artifact lineage are not implemented.
- Selected repair branches do not yet bind exact replacement script or avatar
  versions; provider work remains synthetic.
- The executable `CAPTION_FORMAT` policy does not match the corrected
  TTS-input compatibility failure and must be replaced with test-first domain
  work.
- Raw media is not inspected; current findings are structured inputs.
- `VISUAL_QUALITY` has no automatic detector or threshold yet. It proposes a
  retry for approval from a preclassified finding.
- There is no LLM, API, background worker, UI, or production provider.
- Formatting, linting, and type-checking tools are not configured.

## Next Recommended Action

Write a failing policy test for provider-facing text such as `450*F` being
pronounced literally, then replace the caption-only failure model with the
correct provider/model-specific compatibility boundary. Resolve whether
normalized spoken text is a distinct artifact before building further lineage
on the wrong model.

## Later / Parking Lot

- Stale and out-of-order provider events.
- Full artifact lineage and replacement-version semantics.
- Deterministic spoken-text, caption, compatibility, and visual-quality gates.
- Model-assisted interpretation and the synthetic evaluation dataset.
- API, durable worker, review UI, observability, and polished demo assets.
- Postgres or a durable workflow framework if concurrency evidence requires it.

## Verification

Verified on 2026-09-24:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Result: 17 tests passed.

Repository inspection:

- Branch: `codex/initial-media-qc`.
- Git history: initial feature commit under review in PR #1.
