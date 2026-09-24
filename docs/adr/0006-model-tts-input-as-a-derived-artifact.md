# ADR 0006: Model TTS input as a derived artifact

Status: Accepted

## Context

The observed `450*F` failure was speech, not captions: the selected voice read
the notation as “four hundred fifty star F” instead of the intended temperature.
The initial `CAPTION_FORMAT` policy would repair only captions and leave the
mispronounced video untouched. Rewriting the authored script would also be too
broad when its meaning is already approved.

## Decision

Treat provider-facing TTS input as a derived artifact distinct from the authored
script. `TTS_INPUT_COMPATIBILITY` proposes `REPAIR_TTS_INPUT`, invalidating that
derived input and any video and captions generated from it, but preserving the
authored script, avatar, and voice profile. A replacement input is required
before approval; the policy does not itself guess a rewrite or submit a job.
Caption formatting remains a separate, caption-only failure class.

The future provider adapter or a validator using its declared provider/model
capability profile will prepare and check candidate spoken text. The core
repair policy owns dependency scope and approval rules, not a universal symbol
replacement table. If intent or pronunciation is ambiguous, a human must
resolve it. Each accepted TTS input will need an immutable version tied to the
authored script version and provider/model configuration before executable
approval; that binding belongs to the versioned-artifact work.

## Alternatives considered

- Rewrite the authored script whenever a provider mispronounces notation. This
  conflates creative intent with provider-specific encoding.
- Repair only captions. This cannot change audio already rendered incorrectly.
- Normalize symbols globally. Different providers and models can interpret the
  same notation differently, and a guessed rewrite may change meaning.

## Consequences

The current slice can classify the failure, plan its minimum invalidation, and
block approval while safe replacement text is missing. It does not yet produce
or persist that text, inspect rendered audio, or implement capability profiles.
Those require versioned artifacts and deterministic quality-gate work.
