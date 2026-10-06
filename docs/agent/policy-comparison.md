# Acceptance policy comparison

Validation decides whether model output is well formed and within minimum-repair
scope. An acceptance policy then decides whether a valid diagnosis gets repair
authority or abstains with a clarification request. Policies are versioned
(`AcceptancePolicy` in `agent/interpretation.py`) so the same saved outputs can
be scored under each.

| Version | Authority requires |
| --- | --- |
| `confidence-v1` (default) | Confidence of at least 0.8 and at least one cited fact |
| `grounded-scope-v2` | `confidence-v1`, plus at least one cited fact on an artifact the repair (or any offered creative branch) replaces |

The artifact kind comes from the version ID prefix (`script-`, `tts-`,
`avatar-`, `voice-`, `caption-`, `video:`). Unrecognized IDs never ground a
repair, so the candidate policy fails closed.

## Error types

Each failing case gets exactly one type:

- **False pass**: the decision had repair authority (a finding with a plan or
  creative branches) but failed the saved expectation. This is the unsafe error.
- **False block**: the case expected a diagnosis and nothing wrong was proposed,
  but no repair was authorized: the model abstained, the policy held the correct
  diagnosis, or the provider failed. This costs a human follow-up.
- **Blocked wrong output**: the case expected a diagnosis, and validation
  rejected the output or the policy held a different diagnosis. The safety gate
  worked, though the case still needs a human.
- **Other failure**: correctly held on an ambiguous case, but with the wrong
  citations or clarification type.

Error types come from the turn outcome and raw output, not the score labels.

Ambiguous cases supply no facts, so no current policy can give them authority.

## Recorded result

`python -m media_qc_agent.cli.compare` replays the saved fixtures and seven
recorded live runs under both policies: the September 29 Gemini (unmasked and
masked), Jev, and Luna runs, and three October 6 Luna runs with the current
chat prompt.
Under `confidence-v1`, replay reproduces every recorded failure label, which
checks that current validation still scores the saved outputs the same way.

`grounded-scope-v2` removes both recorded false passes. Gemini classified the
adversarial TTS defect as a caption defect and cited the TTS-input fact, while
the caption repair replaces only captions. The candidate policy holds those
outputs, so they become blocked wrong outputs; false blocks stay at 1 (Jev's
own abstention). No other case changes. The
current default model, Luna, had no false passes or false blocks in any run, so
the policy choice changes nothing for it on these cases.

## CI gate

CI runs the command with `--expect evals/results/policy-comparison-v1.json`
and fails on any difference, printing each changed path. It also fails if any
saved fixture response stops passing under any policy, with or without a
snapshot. A validation, scoring, or policy change that changes a recorded
outcome therefore needs a snapshot update that is visible in the PR diff.

## Limits

- Seven runs over 15 synthetic cases. The counts show what a policy does to
  these saved outputs. They are not accuracy or calibration estimates.
- Replay holds model outputs fixed. A prompt change needs a new paid live run
  before it can be compared. The September runs predate prompt versioning
  (`prompt_version` is `unrecorded`).
- Grounding checks which artifact a fact is on, not whether the fact supports
  the diagnosis. A wrong diagnosis that cites a fact on an in-scope artifact
  still passes.
- Every recorded case stores a SHA-256 digest of the request it answered
  (feedback, version IDs, evidence, after masking when used). Replay fails if a
  case's text changed after recording, so outputs are never scored against
  inputs the model did not see.
- `grounded-scope-v2` is not the default. On these cases it held only wrong
  outputs, but a correct diagnosis citing only an out-of-scope fact would become
  a false block, and no saved case tests that yet.
