# ADR 0011: Repair captions without a video provider job

Status: Accepted

## Context

A caption formatting defect does not imply that speech or video is defective.
The general workflow executor submits approved plans to the video provider,
which would spend money and replace an acceptable video for a caption-only
repair.

## Decision

Treat caption validation as a deterministic check over synthetic timed cues.
Return rule-specific evidence with the cue index, observed value, and limit.
A caption finding references an existing video version whose input lineage
matches the run. After approval, a valid replacement caption version is
recorded locally in one transaction and becomes the run's active caption. The
video pointer and provider-job count do not change. Both executor submission
and direct repository submission reject caption-only plans.

## Alternatives considered

- Submit every repair to the video provider. This invalidates a good video and
  makes caption formatting unnecessarily expensive.
- Silently rewrite captions during validation. Timing and line-break choices
  may need editorial judgment; the validator reports evidence and checks a
  supplied replacement instead.

## Consequences

The current gate checks cue timing, overlap, nonblank text, line count, and
line length for synthetic fixtures. It does not inspect speech, determine
transcript accuracy, or generate replacement wording. A later review surface
can present the evidence and collect the corrected cues. See the
[caption-quality guide](../caption-quality.md).
