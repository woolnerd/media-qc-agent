"""Durable timing boundaries and incomplete observations."""

import unittest

from media_qc_agent.workflow.timing import elapsed_seconds, job_timing


class TimingTests(unittest.TestCase):
    def test_completion_wait_and_total_wait_have_different_boundaries(self) -> None:
        timing = job_timing(
            "2026-10-05T12:00:00Z", "2026-10-05T12:00:10Z", "2026-10-05T12:04:10Z"
        )
        self.assertEqual(timing.approval_to_completion_seconds, 250)
        self.assertEqual(timing.recorded_job_to_completion_seconds, 240)

    def test_queue_wait_uses_first_claim_not_job_recording(self) -> None:
        timing = job_timing(
            "2026-10-05T12:00:00Z",
            "2026-10-05T12:00:10Z",
            None,
            "2026-10-05T12:00:07Z",
        )
        self.assertEqual(timing.approval_to_first_claim_seconds, 7)
        self.assertIsNone(timing.approval_to_completion_seconds)

    def test_pending_and_reversed_timestamps_are_unknown(self) -> None:
        self.assertIsNone(elapsed_seconds(None, "2026-10-05T12:00:00Z"))
        self.assertIsNone(elapsed_seconds("2026-10-05T12:00:00Z", None))
        self.assertIsNone(
            elapsed_seconds("2026-10-05T12:00:01Z", "2026-10-05T12:00:00Z")
        )
