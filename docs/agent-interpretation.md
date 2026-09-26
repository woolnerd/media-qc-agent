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
optional `OPENROUTER_MODEL` override. The initial default is
`qwen/qwen3.5-flash-02-23`, listed at $0.065 per million input tokens and $0.26
per million output tokens when checked on September 26, 2026. Prices can change;
see [OpenRouter model pricing](https://openrouter.ai/qwen/qwen3.5-flash-02-23).

The adapter sends one request with a 30-second timeout, 768 output-token cap,
zero temperature, and a strict JSON schema. Routing requires support for the
requested parameters, following [OpenRouter structured output documentation](https://openrouter.ai/docs/guides/features/structured-outputs).
Incomplete output and transport errors fail without automatic paid retries.
Errors omit keys and response bodies. Live output still requires independent
application validation; schema enforcement does not prove a diagnosis.

Supply the key in your shell environment, never in source or command arguments.
The fake remains the default for offline tests; tests mock OpenRouter transport
and make no paid calls.
