# Workflow timing boundaries

`WorkflowRepository.get_job_timing` derives durations from persisted timestamps
for one provider job, using that job's saved plan to find its approval. It works
across process restarts without requiring an in-memory timer.

| Measurement | Start | End |
| --- | --- | --- |
| Approval to completion | Approval of the job's exact plan | Earliest committed completion report for that job |
| Recorded job to completion | Local provider job creation | Earliest committed completion report for that job |
| Provider submission call | Monotonic time before adapter call | Adapter response or exception; existing execution event |
| Approval to first claim | Approval of the job's exact plan | First committed worker claim for that plan |

The second duration includes rendering and notification delay after local job
recording. It is not pure rendering latency or exact provider acceptance latency.
Recovery after acceptance can record the job later. Provider timestamps would be
needed to measure those boundaries accurately.

Completion timing also exists for stale jobs, so it must not be labeled time to
the current usable video without checking the callback outcome. A duplicate
notification does not move the earliest completion timestamp. Pending completion,
missing approval, or reversed timestamp order yields `None`, not zero. SQLite
timestamps have millisecond precision and use the database host's wall clock;
clock adjustments can distort these durations.

The first claim timestamp is persisted atomically with the claim in
`worker_attempts.first_claimed_at`. Recovery increments the claim count without
overwriting this timestamp, so lease expiry and retry backoff do not inflate
initial queue wait. Preexisting rows without the timestamp remain unknown; do
not infer a first claim from a later recovery. Direct submissions without a
worker claim also have unknown queue wait. The job timing reader exposes this
measurement once a provider job has been recorded.

Provider submission now emits a short OpenTelemetry CLIENT span named
`provider.submit`. It contains only hashed run, plan, submission key, and job
references plus a safe error category on failure. Exception messages and
stack traces are not recorded. Log delivery happens after the span ends.

The API dependency defaults to no exported spans until an application supplies
a tracer provider. Tests inject a local SDK and in-memory exporter; no global
provider is installed by workflow imports. Tracer failures are counted locally
and cannot become submission retries. Configured processors/exporters must be
bounded; synchronous exporter delays can still delay the worker.

The submission span's W3C `traceparent` is saved atomically with the accepted
provider job. No baggage or arbitrary trace state is saved. After a crash before
recording, recovery saves the context of its successful reconciliation call;
an earlier interrupted call may remain an unlinked span.

Callback handling emits a short `workflow.complete` span with a link to the
saved submission context. A link connects separate executions without pretending
the provider render was running inside the worker. The callback retains its own
trace (or the current request's trace). Missing/invalid saved context produces
an unlinked span. Tracing failure does not prevent completion. The span records
the disposition only after commit and ends before completion-log delivery.

Each duplicate delivery may produce another span, while durable event IDs and
replacement counts remain deduplicated. A span can be sampled out or fail to
export; a saved context does not guarantee the viewer has the linked span.
Job/plan/key digests remain useful for event correlation. A submission span ends
when the adapter returns, not when rendering finishes.

API usage follows [OpenTelemetry Python instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/).
