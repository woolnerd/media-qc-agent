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
from media_qc_agent.captions import CaptionCue, UnsafeCaptions, validate_captions
from media_qc_agent.spoken_text import SpokenTextCapabilities
from media_qc_agent.workflow import VideoSources

GOOD_CUES = (
    CaptionCue(start_ms=0, end_ms=1200, text="Heat the oven."),
    CaptionCue(start_ms=1200, end_ms=2500, text="Check the temperature."),
)


class CaptionValidationTests(unittest.TestCase):
    def test_valid_synthetic_cues_have_no_evidence(self) -> None:
        result = validate_captions(GOOD_CUES)

        self.assertTrue(result.valid)
        self.assertEqual(result.evidence, ())

    def test_long_line_and_extra_line_have_grounded_evidence(self) -> None:
        result = validate_captions(
            (
                CaptionCue(
                    0,
                    1200,
                    "This synthetic caption line is longer than forty-two characters."
                    "\nsecond line\nthird line",
                ),
            )
        )

        self.assertEqual(
            {item.rule for item in result.evidence}, {"line_too_long", "too_many_lines"}
        )
        self.assertTrue(all(item.cue_index == 0 for item in result.evidence))
        self.assertIn("42", {item.limit for item in result.evidence})

    def test_overlap_empty_text_and_bad_timing_are_separate_evidence(self) -> None:
        result = validate_captions(
            (
                CaptionCue(0, 1000, "First cue"),
                CaptionCue(900, 900, "   "),
            )
        )

        self.assertEqual(
            {item.rule for item in result.evidence},
            {"overlapping_cues", "invalid_timing", "empty_text"},
        )
        self.assertTrue(all(item.cue_index == 1 for item in result.evidence))

    def test_missing_cues_are_reported(self) -> None:
        result = validate_captions(())

        self.assertFalse(result.valid)
        self.assertEqual(result.evidence[0].rule, "missing_cues")


class CaptionRepairWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.repository = WorkflowRepository(self.connection)
        self.repository.initialize()
        self.repository.create_script_version(
            version_id="script-1", authored_text="Heat the oven. Check the temperature."
        )
        self.repository.create_tts_input_version(
            version_id="tts-1",
            script_version_id="script-1",
            capabilities=SpokenTextCapabilities(
                "synthetic-tts", "literal-v1", frozenset()
            ),
        )
        for version_id, kind in (
            ("avatar-1", ArtifactKind.AVATAR),
            ("voice-1", ArtifactKind.VOICE),
        ):
            self.repository.create_source_version(version_id=version_id, kind=kind)
        self.sources = VideoSources("script-1", "tts-1", "avatar-1", "voice-1")
        self.provider = FakeVideoProvider()
        self.repository.create(
            run_id="video-run",
            finding=QualityFinding(
                kind=FailureKind.VISUAL_QUALITY,
                explanation="Synthetic jerky video",
                confidence=0.9,
            ),
            sources=self.sources,
        )
        self.repository.approve("video-run")
        job_id = (
            WorkflowExecutor(repository=self.repository, provider=self.provider)
            .submit("video-run")
            .external_job_id
        )
        assert job_id is not None
        self.repository.record_completion(
            external_job_id=job_id, external_event_id="video-complete"
        )
        self.video_id = self.repository.get("video-run").active_video_version_id
        assert self.video_id is not None
        self.repository.record_caption_version(
            version_id="caption-1", video_version_id=self.video_id
        )

    def tearDown(self) -> None:
        self.connection.close()

    def create_caption_repair(self) -> None:
        self.repository.create(
            run_id="caption-run",
            finding=QualityFinding(
                kind=FailureKind.CAPTION_FORMAT,
                explanation="Synthetic caption line is too long",
                confidence=1.0,
            ),
            sources=self.sources,
            video_version_id=self.video_id,
        )

    def test_caption_repair_keeps_video_and_makes_no_provider_call(self) -> None:
        self.create_caption_repair()
        self.repository.approve("caption-run")
        attempts_before = self.provider.submit_attempts
        executor = WorkflowExecutor(repository=self.repository, provider=self.provider)

        with self.assertRaisesRegex(ValueError, "caption"):
            executor.submit("caption-run")
        with self.assertRaisesRegex(ValueError, "caption"):
            self.repository.record_submission(
                run_id="caption-run", external_job_id="should-not-exist"
            )
        repaired = self.repository.record_caption_repair(
            run_id="caption-run", version_id="caption-2", cues=GOOD_CUES
        )

        run = self.repository.get("caption-run")
        self.assertEqual(run.status, WorkflowStatus.SUCCEEDED)
        self.assertEqual(run.active_video_version_id, self.video_id)
        self.assertEqual(run.active_caption_version_id, "caption-2")
        self.assertEqual(
            repaired.source_versions, ((ArtifactKind.VIDEO, self.video_id),)
        )
        self.assertEqual(self.repository.get_caption_cues("caption-2"), GOOD_CUES)
        self.assertEqual(
            self.repository.get_artifact_version("caption-1").source_versions,
            ((ArtifactKind.VIDEO, self.video_id),),
        )
        self.assertEqual(self.provider.submit_attempts, attempts_before)

    def test_invalid_repair_stays_ready_and_creates_no_caption(self) -> None:
        self.create_caption_repair()
        self.repository.approve("caption-run")

        with self.assertRaises(UnsafeCaptions) as failure:
            self.repository.record_caption_repair(
                run_id="caption-run",
                version_id="caption-2",
                cues=(
                    CaptionCue(
                        0,
                        1000,
                        "A line that is much too long for this caption gate to accept.",
                    ),
                ),
            )

        self.assertEqual(failure.exception.evidence[0].rule, "line_too_long")
        self.assertEqual(
            self.repository.get("caption-run").status, WorkflowStatus.READY
        )
        with self.assertRaises(KeyError):
            self.repository.get_artifact_version("caption-2")
        self.assertEqual(self.provider.jobs_created, 1)

    def test_repair_requires_approval_and_cannot_be_replayed(self) -> None:
        self.create_caption_repair()
        with self.assertRaisesRegex(ValueError, "ready"):
            self.repository.record_caption_repair(
                run_id="caption-run", version_id="caption-2", cues=GOOD_CUES
            )
        self.repository.approve("caption-run")
        self.repository.record_caption_repair(
            run_id="caption-run", version_id="caption-2", cues=GOOD_CUES
        )
        with self.assertRaisesRegex(ValueError, "ready"):
            self.repository.record_caption_repair(
                run_id="caption-run", version_id="caption-3", cues=GOOD_CUES
            )
        with self.assertRaises(KeyError):
            self.repository.get_artifact_version("caption-3")
        self.assertEqual(self.provider.jobs_created, 1)

    def test_caption_finding_requires_exact_existing_video(self) -> None:
        with self.assertRaisesRegex(ValueError, "video"):
            self.repository.create(
                run_id="caption-run",
                finding=QualityFinding(
                    kind=FailureKind.CAPTION_FORMAT,
                    explanation="Synthetic formatting defect",
                    confidence=1.0,
                ),
                sources=self.sources,
            )


if __name__ == "__main__":
    unittest.main()
