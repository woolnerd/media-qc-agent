# ADR 0026: Default to grounded-scope acceptance

Status: Accepted

## Context

ADR 0025 added versioned acceptance policies and kept `confidence-v1` as the
default. Replaying seven recorded live runs showed `confidence-v1` gave repair
authority to two wrong Gemini outputs: the adversarial TTS defect was diagnosed
as a caption defect while citing only the TTS-input fact. `grounded-scope-v2`
held both and created no new false blocks. It changed nothing for Luna, the
default chat model, which had no false passes or false blocks in four runs.

## Decision

Make `grounded-scope-v2` the default acceptance policy. A valid diagnosis gains
repair authority only if at least one cited fact is on an artifact that the
repair, or an offered creative branch, would replace. Keep `confidence-v1`
selectable for evaluation and comparison.

## Alternatives rejected

- Keep `confidence-v1`: it accepted a wrong, ungrounded repair in the recorded
  runs, and the replay showed no measured cost from the stricter rule.
- Wait for more discriminating cases first: safer data, but the check only
  removes authority, so a wrong hold costs a clarification request, while a
  wrong repair can cost a paid regeneration.

## Consequences

Callers must pass well-formed artifact version IDs; unrecognized IDs never
ground a repair and lead to clarification. A correct diagnosis whose only cited
facts are on artifacts outside the repair scope will now be held. No saved case
covers that yet, so the false-block cost is unmeasured. The chat prompt does not
embed the acceptance policy, so its prompt version is unchanged. Spans and
evaluation records report `grounded-scope-v2`.

See [policy comparison](https://github.com/woolnerd/media-qc-agent/blob/1762b8a69dcd694d6203d7b18b107aae6568be21/docs/agent/policy-comparison.md).
