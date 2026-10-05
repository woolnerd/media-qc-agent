"""Real SDK export, privacy, and failure isolation at the provider boundary."""

import unittest
from unittest.mock import Mock, patch

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode, Tracer

from tests.workflow import test_telemetry


class TracingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = test_telemetry.TelemetryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.addCleanup(self.provider.shutdown)
        self.fixture.observer.tracer = self.provider.get_tracer("test")

    def test_callback_missing_context_exports_unlinked_span(self) -> None:
        self.fixture.worker().run_once()
        with self.fixture.database.connection() as connection:
            connection.execute("UPDATE provider_jobs SET traceparent = NULL")
            connection.commit()
        self.fixture.database.observer = self.fixture.observer
        with self.fixture.database.repository() as repository:
            repository.record_completion(
                external_job_id="video-job-1", external_event_id="trace-event"
            )
        callback = self.exporter.get_finished_spans()[-1]
        self.assertEqual(callback.name, "workflow.complete")
        self.assertEqual(len(callback.links), 0)
        self.assertEqual((callback.attributes or {})["workflow.disposition"], "applied")

    def test_submission_exports_one_short_span_with_safe_job_correlation(self) -> None:
        self.fixture.worker().run_once()
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.name, "provider.submit")
        self.assertEqual(
            set(span.attributes or {}),
            {
                "workflow.run_ref",
                "workflow.plan_ref",
                "workflow.submission_digest",
                "workflow.job_ref",
            },
        )
        self.assertNotIn("video-job-1", str(span.attributes))
        with self.fixture.database.repository() as repository:
            self.assertEqual(repository.get("run-1").status, "submitted")

    def test_callback_new_connection_links_saved_submission_and_records_outcomes(
        self,
    ) -> None:
        self.fixture.worker().run_once()
        submission = self.exporter.get_finished_spans()[0]
        self.fixture.database.observer = self.fixture.observer
        with self.fixture.database.repository() as repository:
            repository.record_completion(
                external_job_id="video-job-1", external_event_id="trace-event"
            )
        with self.fixture.database.repository() as repository:
            repository.record_completion(
                external_job_id="video-job-1", external_event_id="trace-event"
            )
        spans = self.exporter.get_finished_spans()
        self.assertEqual(
            [s.name for s in spans],
            ["provider.submit", "workflow.complete", "workflow.complete"],
        )
        for callback in spans[1:]:
            self.assertEqual(
                callback.links[0].context.trace_id,
                submission.context.trace_id if submission.context else None,
            )
            self.assertEqual(
                callback.links[0].context.span_id,
                submission.context.span_id if submission.context else None,
            )
            self.assertNotEqual(
                callback.context.trace_id if callback.context else None,
                callback.links[0].context.trace_id,
            )
        self.assertEqual((spans[1].attributes or {})["workflow.disposition"], "applied")
        self.assertEqual(
            (spans[2].attributes or {})["workflow.disposition"], "redundant"
        )

    def test_callback_without_submission_context_and_broken_tracer_still_completes(
        self,
    ) -> None:
        tracer = Mock(spec=Tracer)
        tracer.start_span.side_effect = RuntimeError("secret")
        self.fixture.observer.tracer = tracer
        self.fixture.worker().run_once()
        self.fixture.database.observer = self.fixture.observer
        with self.fixture.database.repository() as repository:
            result = repository.record_completion(
                external_job_id="video-job-1", external_event_id="trace-event"
            )
            self.assertEqual(result.status, "succeeded")
        self.assertEqual(self.fixture.observer.trace_failures, 2)

    def test_timeout_records_category_without_exception_text_and_keeps_retry_policy(
        self,
    ) -> None:
        with patch.object(
            self.fixture.provider, "submit", side_effect=TimeoutError("api-key=secret")
        ):
            self.assertEqual(self.fixture.worker().run_once().outcome, "retry_wait")
        span = self.exporter.get_finished_spans()[0]
        self.assertEqual(span.status.status_code, StatusCode.ERROR)
        self.assertEqual((span.attributes or {})["error.type"], "timeout")
        self.assertEqual(len(span.events), 0)
        self.assertIsNone(span.status.description)
        self.assertNotIn("secret", str(span.attributes))

    def test_tracer_start_failure_does_not_prevent_or_repeat_submission(self) -> None:
        tracer = Mock(spec=Tracer)
        tracer.start_span.side_effect = RuntimeError("secret")
        self.fixture.observer.tracer = tracer
        self.assertEqual(self.fixture.worker().run_once().outcome, "submitted")
        self.assertEqual(self.fixture.provider.submit_attempts, 1)
        self.assertEqual(self.fixture.observer.trace_failures, 1)

    def test_span_end_failure_does_not_trigger_provider_retry(self) -> None:
        tracer = Mock(spec=Tracer)
        tracer.start_span.return_value.end.side_effect = RuntimeError("export failed")
        self.fixture.observer.tracer = tracer
        self.assertEqual(self.fixture.worker().run_once().outcome, "submitted")
        self.assertEqual(self.fixture.worker().run_once().outcome, "idle")
        self.assertEqual(self.fixture.provider.submit_attempts, 1)
        self.assertEqual(self.fixture.observer.trace_failures, 1)
