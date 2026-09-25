# ADR 0013: Use synthetic motion jumps for the first visual-quality signal

Status: Accepted

## Context

The workflow accepts an injected `VISUAL_QUALITY` finding but previously had
no defined evidence for it. A reproducible fixture is needed before claiming
that a video has jerky motion. There is no raw-video analyzer or calibrated
perceptual dataset in this milestone.

## Decision

Use synthetic per-frame displacement measurements. Within one declared shot,
an abrupt change is a difference between adjacent displacements greater than
12 pixels per frame. Two or more such changes in at least four samples create
a `VISUAL_QUALITY` finding. Evidence cites the exact video version, frame
pair, observed jump, and threshold. One abrupt change or too few samples
needs reviewer context; declared shot boundaries are excluded. A finding opens
the existing approval gate before any replacement video job is submitted.

## Alternatives considered

- Treat any motion spike as a failure. A single spike may represent an edit or
  measurement noise.
- Ignore shot boundaries. A legitimate cut can look like a large motion jump.
- Claim raw-video or multimodal assessment now. Neither an evaluator nor a
  labeled dataset exists yet to support that claim.

## Consequences

The signal is deterministic and auditable, but the threshold is illustrative,
not calibrated to a provider or human-quality standard. It can miss unnatural
motion without measured jumps and can flag purposeful fast movement. Human or
later multimodal review remains necessary for those cases. See the
[visual-quality signal guide](../visual-quality-signal.md).
