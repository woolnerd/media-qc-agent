# ADR 0012: Use grounded environment metadata before rendering

Status: Accepted

## Context

An oven-related script paired with an office avatar can fail visibly after an
expensive render. The current synthetic slice has script text and artifact IDs
but no image analysis, so a pre-render decision needs explicit metadata and a
traceable reason.

## Decision

Store a script's declared environment and a phrase that actually occurs in its
authored text. Store each avatar version's declared environment. A pure check
compares those exact versions and returns a structured mismatch finding with
the phrase and both environments. A mismatch opens the existing human
clarification flow, which offers script revision or avatar replacement. Video
approval also checks compatibility so another finding cannot bypass this gate.
Neutral metadata means the asset is compatible with any declared environment.

## Alternatives considered

- Infer the environment from arbitrary keywords. The same word can be used
  figuratively, and a keyword alone does not prove scene requirements.
- Evaluate raw images in this milestone. There is no bounded visual evaluator
  or labeled image dataset yet.
- Automatically change the script or avatar. That would choose creative intent
  without a person.

## Consequences

The fixture is explainable and testable before rendering, but its result is
only as good as the supplied metadata. It does not verify what an avatar image
actually depicts or classify a new script without metadata. Later image or
model evaluation should provide its own evidence and accuracy measurements.
See the [synthetic fixture guide](../quality/environment-compatibility.md).
