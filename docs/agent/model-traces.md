# Model traces and evaluation records

Each interpretation is one traced turn: a model call followed by strict
validation and the deterministic repair policy. `interpret_traced` returns the
turn and emits spans; it never changes the interpretation, and provider or
validation errors become outcomes rather than exceptions.

## Model identity

Every `ModelProvider` reports `ModelIdentity(provider, model, prompt_version)`.
Prompt versions are content hashes: the chat version covers the system prompt
and output schema; the Jev version covers its question templates and criteria.
Editing either produces a new version without a manual label. Model choice is a
separate field, so a model can change while the prompt version stays fixed.
Fixture replay reports `fake/fixture/fixture`.

The acceptance policy that decides authority versus abstention is versioned
separately (`confidence-v1`, `grounded-scope-v2`). It is not part of the
prompt, so turns, spans, and evaluation records carry `policy_version` too.

The chat prompt embeds the repair policy, so a policy change also changes the
chat prompt version. Sampling parameters are not part of the version.

## Spans

| Span | Kind | Attributes |
| --- | --- | --- |
| `agent.interpret` | Internal | `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `agent.prompt.version`, `agent.policy.version`, `agent.artifact_refs`, optional `workflow.run_ref`, `agent.outcome` |
| `agent.diagnose` | Client | `error.type=provider_error` on failure |
| `agent.plan` | Internal | `agent.outcome`, `agent.failure_kind`, `agent.repair_actions`, `agent.clarification`, `agent.cited_evidence`; `error.type=validation_rejected` on rejection |
| `eval.case` | Internal, evaluator only | `eval.case_id`, `eval.category`, `eval.passed`, `eval.failures` |

Outcomes are `accepted` (diagnosis with a plan or creative choice), `abstained`
(diagnostic clarification), `rejected` (failed validation or policy), and
`provider_error`. Separating diagnosis from planning shows whether a failure came
from the model call or from application validation.

Spans follow the [workflow observability](../architecture/observability.md)
rules: allowlisted attributes, SHA-256 references for artifact and run IDs, and
no feedback, evidence, explanation, raw output, or exception text. Exceptions
are not recorded on spans because validation messages can echo model output.
Tracer failures produce no span and do not affect the result.

## Linking to workflows

Artifact references cover every version supplied to the model. When a turn's
finding creates a run, pass `run_id`: `workflow.run_ref` then matches the
`provider.submit` spans for that run, joining "the model diagnosed X" to "the
workflow paid for render Y". The demo does this. The link is a shared reference,
not a parent span, because diagnosis finishes before the run exists.

## Evaluation records

`python -m media_qc_agent.cli.evaluate --traces PATH` writes one JSON line per
case: model identity, `trace_id`, exact artifact versions, feedback, evidence,
raw output, outcome, validated interpretation, policy version, and score. The records hold
full content because the evaluation cases are synthetic. Do not write records
for real review data. `trace_id` is null unless a tracer SDK is configured.

Score labels are unchanged from the saved September results, so new runs remain
comparable. The `outcome` field distinguishes rejections from provider errors,
which share the `provider_or_validation_error` score label.

## Limits

Traces describe what a model returned and how policy handled it; they are not
accuracy measurements. Fixture records test wiring only. Comparing prompt or
policy versions on recorded outputs is described in
[policy comparison](policy-comparison.md).
