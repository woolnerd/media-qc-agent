# Agent interpretation boundary

`model.ModelProvider` accepts review feedback, exact artifact version IDs, and
available evidence. It returns raw structured-output text. Model names, API
credentials, prompts and transport belong to an adapter, outside this contract.
The interface grants no persistence, approval or media-provider access.

`FakeModelProvider` copies an exact request-to-response fixture mapping. Repeated
requests return the same output; unknown feedback, versions or evidence fail
explicitly. Tests and synthetic scenarios can also supply malformed output to
exercise the validation boundary. This fake performs no language understanding
and makes no claims about live model quality.


## OpenRouter

`OpenRouterModelProvider.from_environment()` reads `OPENROUTER_API_KEY` and an
optional `OPENROUTER_MODEL` override. The low-cost pilot default is
`openai/gpt-6-luna`, listed at $0.10 per million input tokens and $0.50 per
million output tokens when checked on September 29, 2026. Prices can change;
see [OpenRouter model pricing](https://openrouter.ai/openai/gpt-6-luna) and
[official OpenAI model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna).

The adapter sends one request with a 30-second timeout, 768 output-token cap,
and a strict JSON schema. Luna omits `temperature` because OpenRouter's strict
parameter routing returned HTTP 404 when it was present. Other chat models,
including the previous Gemini default, retain zero temperature. Routing
requires support for the requested parameters, following
[OpenRouter structured output documentation](https://openrouter.ai/docs/guides/features/structured-outputs).
Incomplete output and transport errors fail without automatic paid retries.
Errors omit keys and response bodies. Live output still requires independent
application validation; schema enforcement does not prove a diagnosis.

Supply the key in your shell environment, never in source or command arguments.
The fake remains the default for offline tests; tests mock OpenRouter transport
and make no paid calls.


## Classification contract

The six required fields are `kind` (known failure string or null), `explanation`
(nonblank text), `confidence` (finite number from 0 to 1), `evidence_indices`
(unique zero-based indices into supplied evidence), `action` (known action or
null), and `invalidates` (unique artifact kinds). Extra fields, duplicate keys,
unknown values, malformed JSON and out-of-range references are rejected.

`interpret_feedback` independently validates output after any provider call.
Confidence below 0.8, no cited facts, or a null diagnosis yields clarification
with no finding or executable decision. Unsupported diagnoses are rejected.
Every proposal must match the exact action and invalidations from `plan_repair`;
overrepair and underrepair both fail. An environment mismatch cannot select a
creative branch; deterministic policy returns its human clarification request.

Evidence indices preserve supplied observations and version IDs. The model's
explanation is a diagnosis, not a newly observed fact. Structural grounding does
not prove semantic relevance or real-media quality. Repository lineage checks
still apply when a validated finding is saved, and human approval remains
required before execution.

Run the offline interpretation demo:

```bash
PYTHONPATH=src python3 -m media_qc_agent.cli.review
```

With `OPENROUTER_API_KEY` already set in your shell, explicitly opt into one paid
interpretation request:

```bash
PYTHONPATH=src python3 -m media_qc_agent.cli.review --live
```

This command prints validated interpretation only. The existing durable workflow
demo now uses the fake interpretation boundary before persisting its finding.
