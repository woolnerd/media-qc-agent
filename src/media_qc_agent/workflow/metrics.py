"""Bounded metric labels; correlation identifiers belong in events and spans."""

from opentelemetry.metrics import Meter

from media_qc_agent.workflow.telemetry import ExecutionEvent


class ExecutionMetrics:
    def __init__(self, meter: Meter) -> None:
        self.events = meter.create_counter("workflow.observed.events", unit="{event}")
        self.duration = meter.create_histogram("workflow.operation.duration", unit="s")

    def record(self, event: ExecutionEvent) -> None:
        labels = {"event.kind": event.kind.value}
        if event.disposition is not None:
            labels["workflow.disposition"] = event.disposition.value
        if event.outcome is not None:
            labels["worker.outcome"] = event.outcome.value
        if event.error_category is not None:
            labels["error.type"] = event.error_category.value
        self.events.add(1, labels)
        if event.duration_seconds is not None:
            self.duration.record(event.duration_seconds, labels)
