"""Metrics aggregate operations without identifier labels or retry side effects."""

import unittest
from unittest.mock import patch

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import Histogram, InMemoryMetricReader

from media_qc_agent.workflow.metrics import ExecutionMetrics
from media_qc_agent.workflow.telemetry import EventKind
from tests.workflow import test_telemetry


class MetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = test_telemetry.TelemetryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.reader = InMemoryMetricReader()
        self.provider = MeterProvider(metric_readers=[self.reader])
        self.addCleanup(self.provider.shutdown)
        self.metrics = ExecutionMetrics(self.provider.get_meter("test"))
        self.fixture.observer.metric_sink = self.metrics.record

    def test_timeout_exports_bounded_counts_duration_and_retry_outcome(self) -> None:
        with patch.object(
            self.fixture.provider, "submit", side_effect=TimeoutError("secret")
        ):
            self.assertEqual(self.fixture.worker().run_once().outcome, "retry_wait")
        data = self.reader.get_metrics_data()
        assert data is not None
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        counts = next(m for m in metrics if m.name == "workflow.observed.events")
        labels = [dict(p.attributes or {}) for p in counts.data.data_points]
        self.assertIn(
            {"event.kind": "provider.submit.error", "error.type": "timeout"}, labels
        )
        self.assertIn(
            {"event.kind": "worker.finished", "worker.outcome": "retry_wait"}, labels
        )
        for attributes in labels:
            self.assertFalse(any("ref" in key or "digest" in key for key in attributes))
        retry = next(
            e for e in self.fixture.events if e.kind is EventKind.WORKER_RESULT
        )
        self.assertEqual(retry.retry_delay_seconds, 2)
        duration = next(m for m in metrics if m.name == "workflow.operation.duration")
        assert isinstance(duration.data, Histogram)
        self.assertEqual(sum(p.count for p in duration.data.data_points), 2)

    def test_metric_sink_failure_does_not_retry_accepted_job(self) -> None:
        def broken(event: object) -> None:
            raise RuntimeError("secret")

        self.fixture.observer.metric_sink = broken
        self.assertEqual(self.fixture.worker().run_once().outcome, "submitted")
        self.assertEqual(self.fixture.worker().run_once().outcome, "idle")
        self.assertEqual(self.fixture.provider.submit_attempts, 1)
        self.assertEqual(self.fixture.observer.metric_failures, 6)

    def test_callback_histogram_records_handling_duration_and_one_artifact_event(
        self,
    ) -> None:
        self.fixture.worker().run_once()
        self.fixture.database.observer = self.fixture.observer
        with (
            patch.object(
                self.fixture.observer, "clock", side_effect=[10.0, 13.0, 20.0, 21.0]
            ),
            self.fixture.database.repository() as repository,
        ):
            repository.record_completion(
                external_job_id="video-job-1", external_event_id="metric-event"
            )
            repository.record_completion(
                external_job_id="video-job-1", external_event_id="metric-event"
            )
        data = self.reader.get_metrics_data()
        assert data is not None
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        duration = next(m for m in metrics if m.name == "workflow.operation.duration")
        assert isinstance(duration.data, Histogram)
        points = [
            p
            for p in duration.data.data_points
            if (p.attributes or {}).get("event.kind") == "workflow.completion.recorded"
        ]
        self.assertEqual(sum(p.count for p in points), 2)
        self.assertEqual(sum(p.sum for p in points), 4)
        self.assertEqual(
            self.fixture.observer.counts()[EventKind.ARTIFACT_CREATED.value], 1
        )
