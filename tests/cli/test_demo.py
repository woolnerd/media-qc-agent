import unittest

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from media_qc_agent.cli.demo import run_demo
from media_qc_agent.domain.ids import reference


class DemoTests(unittest.TestCase):
    def test_demo_exercises_stale_callback_and_lineage(self) -> None:
        result = run_demo()

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["old_event"], "stale")
        self.assertEqual(result["new_event"], "applied")
        self.assertEqual(result["caption_source_video"], result["active_video"])
        self.assertEqual(
            result["video_sources"],
            {
                "script": "script-1",
                "tts_input": "tts-1",
                "avatar": "avatar-1",
                "voice": "voice-1",
            },
        )

    def test_diagnosis_trace_joins_the_run_it_created(self) -> None:
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        self.addCleanup(provider.shutdown)
        result = run_demo(tracer=provider.get_tracer("test"))

        spans = exporter.get_finished_spans()
        diagnosis = next(s for s in spans if s.name == "agent.interpret")
        submissions = [s for s in spans if s.name == "provider.submit"]
        attributes = dict(diagnosis.attributes or {})
        self.assertEqual(attributes["workflow.run_ref"], reference("demo-run"))
        self.assertEqual(attributes["agent.outcome"], "accepted")
        self.assertIn(
            reference(str(result["observed_video"])), attributes["agent.artifact_refs"]
        )
        self.assertEqual(len(submissions), 2)
        for submission in submissions:
            self.assertEqual(
                (submission.attributes or {})["workflow.run_ref"],
                attributes["workflow.run_ref"],
            )


if __name__ == "__main__":
    unittest.main()
