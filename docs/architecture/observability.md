# Workflow observability

Workflow state and audit records remain authoritative. Execution observations
are best effort and cannot initiate provider calls or undo database commits.
Model-turn spans and evaluation records follow the same attribute rules; see
[model traces](../agent/model-traces.md).

## Events and commit boundaries

`ExecutionObserver` accepts independent event and metric sinks plus an
OpenTelemetry tracer. Repository events for approval, reservation, new attempts,
completion, and generated video/caption artifacts are emitted after commit.
The executor records provider-call start, response/error, and saved submission.
The worker records immutable-plan claims and outcomes: submitted, retry wait,
stopped, lease lost, interrupted, or failed. Retry-wait events include the policy
delay. Worker execution durations include local persistence and submission;
they do not include provider rendering.

Artifact events describe replacement video/caption creation and promotion, not
every synthetic fixture seeded into the database. Read artifact lineage for the
exact input versions. Callback `redundant` means duplicate handling; replaying an
existing event ID retains its original audit row while observing another delivery.

Identifiers use SHA-256 references in telemetry. These support correlation but
are not anonymization for guessable values. No script, feedback, evidence text,
credentials, arbitrary provider responses, or exception payloads are emitted.
Event fields are explicitly defined; there is no arbitrary metadata bag.

## Metrics versus durable accounting

`ExecutionMetrics` adapts events to an injected OpenTelemetry meter:

| Instrument | Meaning | Labels |
| --- | --- | --- |
| `workflow.observed.events` | Observed event count | Event kind, disposition, worker outcome, safe error category |
| `workflow.operation.duration` | Provider call, worker execution, callback handling duration in seconds | Same bounded categories |

Run IDs, job IDs, plan IDs, key hashes, event IDs, attempts, and artifact IDs are
not metric labels. Observed counts can be lost during crashes/export failures
and process-local counters reset on restart. They are not billing or unique-job
accounting. Duration histograms allow a backend to summarize distributions;
this application does not calculate population percentiles from tiny fixtures.

`GET /runs/{run_id}/observability` reads durable job/completion-report/audit-row/
replacement counts and per-job timings. Count queries use one SQL statement;
the endpoint's counts and timing list are separate reads and can change during
concurrent activity. Distinct completion reports are provider claims, not proof
of rendering charges. Stale jobs count as completed work without creating an
applied replacement. Audit rows do not count all repeated deliveries.

See [timing and linked spans](workflow-timing.md). Initial approval-to-claim wait
and approval/job-to-completion durations are reconstructed from database records.
They are not pushed as a fresh metric sample on each read or duplicate delivery.

## Configuration

The worker CLI's `--events` flag prints structured event JSON to stderr, keeping
poll results on stdout. The HTTP app accepts an observer via `create_app`.
The API dependency does not install a global tracer/meter provider or exporter.
The dev SDK enables local verification; deploying an SDK/exporter is an explicit
assembly choice. For a configured SDK, inject its tracer and meter:

```python
metrics = ExecutionMetrics(meter_provider.get_meter("media_qc_agent.workflow"))
observer = ExecutionObserver(
    stderr_event,
    tracer=tracer_provider.get_tracer("media_qc_agent.workflow"),
    metric_sink=metrics.record,
)
app = create_app(database_path, observer=observer)
worker = DurableWorker(database, provider, owner="worker-a", observer=observer)
```

Own SDK shutdown/flush at the assembly boundary. Tests use local providers and
in-memory exporters, without modifying global providers. Sink/tracer exceptions
are isolated and locally counted. Synchronous callbacks/exporters can delay
execution: configure fast bounded sinks and buffered exporters where needed.
Observability is not guaranteed delivery, provider health checking, cancellation,
render quality certification, or retry authority.
