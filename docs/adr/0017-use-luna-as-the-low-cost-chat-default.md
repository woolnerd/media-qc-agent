# 0017: Use Luna as the low-cost chat default

Status: Accepted

The initial chat adapter used Gemini 3.1 Flash Lite as a low-cost starting model.
The matched synthetic evaluation found that it proposed a caption-only repair
for a spoken-text TTS defect. A follow-up run on the same masked cases found
GPT-6 Luna correct on that case, with the same 14/15 complete-case score and
about 2.6 times lower reported cost, though about twice the median latency.
Luna's one miss omitted an uncertainty citation while asking for clarification
and proposing no repair.

Use `openai/gpt-6-luna` as the pilot chat default. Omit `temperature` only for
that model: OpenRouter's strict parameter routing returned HTTP 404 with it,
and a compatible request succeeded without it. Keep required-parameter routing,
the JSON schema, response-size and timeout bounds, independent validation, and
human approval. Preserve `OPENROUTER_MODEL` overrides; the Gemini override
retains zero temperature. Jev remains an optional typed-classification adapter.

Keeping Gemini as the default was cheaper to implement but missed the observed
TTS diagnosis and cost more per run. Making Jev the default was deferred because
it uses a different API and cannot write explanations. This decision revises
the default-model choice discussed in ADR 0016, based on an explicit follow-up
to make Luna usable. Fifteen synthetic cases do not measure production accuracy;
the pilot needs broader labeled evaluation before real-media reliance.
