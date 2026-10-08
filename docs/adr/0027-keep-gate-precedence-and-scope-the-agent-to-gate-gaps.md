# ADR 0027: Keep gate precedence and scope the model to gate gaps

Status: Accepted

## Context

Issue #28 asks whether model-assisted interpretation improves the workflow over
deterministic gates plus human review, and which behavior accounts for any
gain. The only model behavior in the code is one structured interpretation
turn. Clarification and replacement reassessment are deterministic, and no
component chooses which evidence to inspect.

We pre-registered 19 held-out cases, ground truth, metrics, modeled human
effort weights, and a decision rule, then recorded three live Luna runs. The
rules-only, single-shot, and layered arms (gates first, model on gate-silent
cases) shared the same gates, repair policy, and approval path.

## Decision

Keep the layered design: deterministic gate findings take precedence, and the
model interprets review feedback only when no gate has a finding. Run the
comparison in CI against a recorded snapshot. Do not build an evidence-selecting
agent loop until a dataset includes evidence that exists but is not
precomputed, and the experiment shows a gain from selecting it.

## Evidence

By construction, every gate finding in the cases is correct, so the comparison
cannot show the model correcting a gate. Both model arms passed the adoption
rule: no safety failures, and 34% fewer modeled human minutes than rules-only
in every run. All of the gain came from
correct routing on six gate-gap cases. Single-shot routing matched layered on
safety and human time, because Luna handled every gate-detected adversarial
case. Layered used 33 model calls instead of 57 and kept pre-render repair
authority with the pre-render gate.

## Alternatives rejected

- **Rules-only:** safe on these cases, but every gate gap costs a human
  diagnosis round. The verdict holds if a diagnosis takes at least about
  2.3 minutes.
- **Single-shot routing for every case:** no measured gain over layered, and
  it makes gate-covered safety depend on the model resisting adversarial
  feedback, which no run tested to failure.
- **Build an evidence-selecting agent now:** every ambiguous case still went
  to a human because the evidence did not settle it. No measured cost here is
  attributable to missing evidence selection.

## Consequences

The model's justified scope is narrow: route review feedback about defects the
gates do not cover. Better gates shrink that scope. A test fails when the chat
prompt, schema, or default model no longer matches the recorded runs, so such a
change needs new live runs, which must keep zero false passes on the held-out
set. Under the pre-registered rule, a single wrong gate-gap diagnosis rejects both model arms.
Changing the cases, gates, policy, or scoring changes the CI snapshot visibly.
Human effort remains modeled. Validating productivity needs timed, blinded
human review of permitted workflow data.

See [baseline comparison](https://github.com/woolnerd/media-qc-agent/blob/1762b8a/docs/agent/baseline-comparison.md).
