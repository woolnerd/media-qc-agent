import sqlite3
import unittest

from media_qc_agent import (
    ArtifactKind,
    FailureKind,
    FakeVideoProvider,
    QualityFinding,
    WorkflowExecutor,
    WorkflowRepository,
    WorkflowStatus,
)
from media_qc_agent.domain.evidence import EvidenceRole
from media_qc_agent.quality.environment import Environment, ScriptScene
from media_qc_agent.quality.spoken_text import SpokenTextCapabilities
from media_qc_agent.quality.visual_quality import MotionSample, check_jerky_video
from media_qc_agent.workflow.models import VideoSources

SMOOTH = (
    MotionSample(0, 4.0),
    MotionSample(1, 5.0),
    MotionSample(2, 4.0),
    MotionSample(3, 5.0),
    MotionSample(4, 4.0),
)
JERKY = (
    MotionSample(0, 4.0),
    MotionSample(1, 5.0),
    MotionSample(2, 30.0),
    MotionSample(3, 4.0),
    MotionSample(4, 5.0),
)


class VisualSignalTests(unittest.TestCase):
    def test_two_abrupt_motion_changes_create_grounded_finding(self) -> None:
        result = check_jerky_video("video:job-1", JERKY)

        assert result.finding is not None
        self.assertEqual(result.finding.kind, FailureKind.VISUAL_QUALITY)
        self.assertEqual(len(result.evidence), 2)
        self.assertEqual(
            {(item.from_frame, item.to_frame) for item in result.evidence},
            {(1, 2), (2, 3)},
        )
        self.assertTrue(
            all(item.video_version_id == "video:job-1" for item in result.evidence)
        )
        self.assertTrue(
            all(item.jump_px_per_frame > item.threshold_px for item in result.evidence)
        )
        self.assertFalse(result.review_needed)
        self.assertIn("illustrative", result.demo_notice)
        self.assertIn("does not inspect video frames", result.demo_notice)
        self.assertIn("illustrative motion-jump signal", result.finding.explanation)

    def test_smooth_motion_passes_and_one_spike_needs_review(self) -> None:
        smooth = check_jerky_video("video:job-1", SMOOTH)
        borderline = check_jerky_video(
            "video:job-1",
            (
                MotionSample(0, 4.0),
                MotionSample(1, 5.0),
                MotionSample(2, 20.0),
                MotionSample(3, 19.0),
            ),
        )

        self.assertIsNone(smooth.finding)
        self.assertFalse(smooth.review_needed)
        self.assertIsNone(borderline.finding)
        self.assertTrue(borderline.review_needed)
        self.assertEqual(len(borderline.evidence), 1)

    def test_declared_shot_boundary_is_not_counted_as_jerk(self) -> None:
        result = check_jerky_video(
            "video:job-1",
            (
                MotionSample(0, 4.0),
                MotionSample(1, 5.0),
                MotionSample(2, 30.0, shot_boundary=True),
                MotionSample(3, 4.0),
                MotionSample(4, 5.0),
            ),
        )

        self.assertIsNone(result.finding)
        self.assertEqual(result.evidence, ())

    def test_noncontiguous_or_negative_motion_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "contiguous"):
            check_jerky_video(
                "video:job-1", (MotionSample(0, 4.0), MotionSample(2, 5.0))
            )
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            check_jerky_video(
                "video:job-1", (MotionSample(0, 4.0), MotionSample(1, -1.0))
            )


class VisualSignalWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.repository = WorkflowRepository(self.connection)
        self.repository.initialize()
        self.repository.create_script_version(
            version_id="script-1",
            authored_text="A synthetic sentence.",
            scene=ScriptScene(Environment.NEUTRAL),
        )
        self.repository.create_tts_input_version(
            version_id="tts-1",
            script_version_id="script-1",
            capabilities=SpokenTextCapabilities(
                "synthetic-tts", "literal-v1", frozenset()
            ),
        )
        self.repository.create_avatar_version(
            version_id="avatar-1", environment=Environment.NEUTRAL
        )
        self.repository.create_source_version(
            version_id="voice-1", kind=ArtifactKind.VOICE
        )
        self.sources = VideoSources("script-1", "tts-1", "avatar-1", "voice-1")
        observed_video_id = self.repository.create_synthetic_video_version(
            fixture_job_id="observed-fixture", sources=self.sources
        ).id
        self.provider = FakeVideoProvider()
        self.executor = WorkflowExecutor(
            repository=self.repository, provider=self.provider
        )
        self.repository.create(
            run_id="first-run",
            finding=QualityFinding(
                kind=FailureKind.VISUAL_QUALITY,
                explanation="Injected synthetic defect for initial video",
                confidence=0.9,
            ),
            sources=self.sources,
            observed_artifact_version_id=observed_video_id,
        )
        self.repository.approve(
            "first-run",
            plan_version_id=self.repository.get("first-run").plan_version_id,
        )
        job_id = self.executor.submit("first-run").external_job_id
        assert job_id is not None
        self.repository.record_completion(
            external_job_id=job_id, external_event_id="first-completion"
        )
        video_id = self.repository.get("first-run").active_video_version_id
        assert video_id is not None
        self.video_id = video_id

    def tearDown(self) -> None:
        self.connection.close()

    def test_signal_opens_approval_before_retrying_exact_video(self) -> None:
        run = self.repository.create_visual_quality_run(
            run_id="retry-run", video_version_id=self.video_id, samples=JERKY
        )

        assert run is not None
        records = self.repository.get_quality_evidence("finding-retry-run")
        self.assertEqual(
            [record.role for record in records[:2]], [EvidenceRole.FACT] * 2
        )
        self.assertTrue(
            all(record.artifact_version_id == self.video_id for record in records)
        )
        self.assertEqual(records[-1].role, EvidenceRole.UNCERTAINTY)
        self.assertIn("does not inspect video frames", records[-1].statement)
        self.assertEqual(run.status, WorkflowStatus.AWAITING_APPROVAL)
        self.assertEqual(run.active_video_version_id, self.video_id)
        with self.assertRaisesRegex(ValueError, "approved"):
            self.executor.submit("retry-run")
        self.assertEqual(self.provider.jobs_created, 1)

        self.repository.approve(
            "retry-run",
            plan_version_id=self.repository.get("retry-run").plan_version_id,
        )
        next_job = self.executor.submit("retry-run").external_job_id
        assert next_job is not None
        self.assertEqual(
            self.repository.get("retry-run").active_video_version_id, self.video_id
        )
        self.repository.record_completion(
            external_job_id=next_job, external_event_id="retry-completion"
        )
        self.assertEqual(self.provider.jobs_created, 2)
        self.assertNotEqual(
            self.repository.get("retry-run").active_video_version_id, self.video_id
        )
        self.assertEqual(
            self.repository.get_artifact_version(self.video_id).kind, ArtifactKind.VIDEO
        )

    def test_borderline_signal_creates_no_automatic_run(self) -> None:
        result = self.repository.create_visual_quality_run(
            run_id="borderline-run", video_version_id=self.video_id, samples=SMOOTH
        )

        self.assertIsNone(result)
        with self.assertRaises(KeyError):
            self.repository.get("borderline-run")
        self.assertEqual(self.provider.jobs_created, 1)


if __name__ == "__main__":
    unittest.main()
