# ADR 0023: Observe workflow without controlling it

## Context

Provider acceptance, local recording, and completion happen at different times.
Recovery can call a provider twice for one unique job. Duplicate and stale
callbacks must be explainable without inflating replacement or cost accounting.

## Decision

Keep durable workflow and callback audit state authoritative. Emit allowlisted
structured observations after commits, separately from provider response events.
Use short OpenTelemetry spans, persisting only W3C traceparent with provider jobs
and linking callback spans to their saved submission context. Reconstruct long
waits from durable timestamps. Instrument aggregate event counts and operation
duration histograms using bounded labels, without per-job metric dimensions.

Isolate observation failures from business retries. Keep SDK/exporter setup at
assembly edges; imports do not configure global providers or contact a service.
Model evaluation, live dashboards, and attempt attribution implementation remain
separate requirements.

## Consequences

Crashes may lose observations. Durable counts can reconcile unique recorded jobs,
completion reports, and replacements, but cannot reconstruct every callback
delivery. Trace links may point to spans not exported or retained. Hashes are
correlation references, not anonymization. Synchronous sinks need bounded latency.
The database adds optional trace context and first-claim timing fields; missing
historical values remain unknown rather than being inferred.

See [observability](../architecture/observability.md) for configuration and limits.
