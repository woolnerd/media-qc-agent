"""Short provider spans with allowlisted attributes and isolated tracer failures."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from opentelemetry.trace import (
    Link,
    Span,
    SpanKind,
    StatusCode,
    Tracer,
    get_current_span,
    set_span_in_context,
)
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from media_qc_agent.workflow.models import WorkflowRun
from media_qc_agent.workflow.telemetry import ErrorCategory, reference


class ProviderSpan:
    def __init__(self, span: Span | None, failure: Callable[[], None]) -> None:
        self.span = span
        self.failure = failure

    def traceparent(self) -> str | None:
        if self.span is None:
            return None
        try:
            carrier: dict[str, str] = {}
            TraceContextTextMapPropagator().inject(
                carrier, context=set_span_in_context(self.span)
            )
            return carrier.get("traceparent")
        except Exception:  # noqa: BLE001 — context capture is observational
            self.failure()
            return None

    def outcome(self, disposition: str) -> None:
        if self.span is not None:
            try:
                self.span.set_attribute("workflow.disposition", disposition)
            except Exception:  # noqa: BLE001 — trace failure must not undo completion
                self.failure()

    def response(self, job_id: str) -> None:
        if self.span is not None:
            try:
                self.span.set_attribute("workflow.job_ref", reference(job_id) or "")
            except Exception:  # noqa: BLE001 — tracing must not retry business work
                self.failure()

    def error(self, category: ErrorCategory) -> None:
        if self.span is not None:
            try:
                self.span.set_attribute("error.type", category.value)
                self.span.set_status(StatusCode.ERROR)
            except Exception:  # noqa: BLE001 — never expose exception payloads
                self.failure()


@contextmanager
def provider_span(
    tracer: Tracer, run: WorkflowRun, failure: Callable[[], None]
) -> Iterator[ProviderSpan]:
    span = None
    try:
        span = tracer.start_span(
            "provider.submit",
            kind=SpanKind.CLIENT,
            attributes={
                "workflow.run_ref": reference(run.id) or "",
                "workflow.plan_ref": reference(run.plan_version_id) or "",
                "workflow.submission_digest": reference(run.idempotency_key) or "",
            },
        )
    except Exception:  # noqa: BLE001 — tracer failure must not prevent submission
        failure()
    try:
        yield ProviderSpan(span, failure)
    finally:
        if span is not None:
            try:
                span.end()
            except Exception:  # noqa: BLE001 — export failure must not retry submission
                failure()


@contextmanager
def completion_span(
    tracer: Tracer, job_id: str, traceparent: str | None, failure: Callable[[], None]
) -> Iterator[ProviderSpan]:
    span = None
    try:
        context = TraceContextTextMapPropagator().extract(
            {"traceparent": traceparent or ""}
        )
        previous = get_current_span(context).get_span_context()
        span = tracer.start_span(
            "workflow.complete",
            kind=SpanKind.INTERNAL,
            attributes={"workflow.job_ref": reference(job_id) or ""},
            links=[Link(previous)] if previous.is_valid else [],
        )
    except Exception:  # noqa: BLE001 — instrumentation must not prevent completion
        failure()
    try:
        yield ProviderSpan(span, failure)
    finally:
        if span is not None:
            try:
                span.end()
            except Exception:  # noqa: BLE001 — instrumentation must not undo completion
                failure()
