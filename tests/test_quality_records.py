import sqlite3
import unittest

from media_qc_agent import ArtifactKind, FailureKind, QualityFinding, WorkflowRepository
from media_qc_agent.captions import CaptionCue
from media_qc_agent.environment import Environment, ScriptScene
from media_qc_agent.quality_records import EvidenceInput, EvidenceRole
from media_qc_agent.spoken_text import SpokenTextCapabilities
from media_qc_agent.workflow import VideoSources


class QualityRecordTests(unittest.TestCase):
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
        self.video = self.repository.create_synthetic_video_version(
            fixture_job_id="fixture-1", sources=self.sources
        )

    def tearDown(self) -> None:
        self.connection.close()

    def finding(self) -> QualityFinding:
        return QualityFinding(
            kind=FailureKind.VISUAL_QUALITY,
            explanation="Synthetic video appears jerky",
            confidence=0.85,
        )

    def test_finding_and_typed_evidence_survive_repository_restart(self) -> None:
        evidence = (
            EvidenceInput(
                EvidenceRole.FACT,
                self.video.id,
                "Frames 1 to 2 change displacement by 25 px per frame",
                observed="25 px/frame",
                limit="12 px/frame",
            ),
            EvidenceInput(
                EvidenceRole.INFERENCE,
                self.video.id,
                "Repeated jumps suggest jerky motion",
            ),
            EvidenceInput(
                EvidenceRole.UNCERTAINTY,
                self.video.id,
                "Camera motion has not been independently reviewed",
            ),
        )
        self.repository.create(
            run_id="run-1",
            finding=self.finding(),
            sources=self.sources,
            video_version_id=self.video.id,
            evidence=evidence,
        )

        restarted = WorkflowRepository(self.connection)
        finding = restarted.get_quality_finding("run-1")
        records = restarted.get_quality_evidence(finding.id)
        self.assertEqual(finding.id, "finding-run-1")
        self.assertEqual(finding.artifact_version_id, self.video.id)
        self.assertEqual(finding.kind, FailureKind.VISUAL_QUALITY)
        self.assertEqual(
            [item.role for item in records],
            [EvidenceRole.FACT, EvidenceRole.INFERENCE, EvidenceRole.UNCERTAINTY],
        )
        self.assertTrue(
            all(item.artifact_version_id == self.video.id for item in records)
        )
        self.assertEqual(records[0].observed, "25 px/frame")
        self.assertEqual(records[0].limit, "12 px/frame")

    def test_unverified_injected_finding_is_labeled_as_such(self) -> None:
        self.repository.create(
            run_id="run-1",
            finding=self.finding(),
            sources=self.sources,
            video_version_id=self.video.id,
        )

        records = self.repository.get_quality_evidence("finding-run-1")
        self.assertEqual(
            {record.role for record in records},
            {EvidenceRole.INFERENCE, EvidenceRole.UNCERTAINTY},
        )
        self.assertTrue(
            all(record.artifact_version_id == self.video.id for record in records)
        )

    def test_wrong_observed_artifact_or_unrelated_evidence_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "observed artifact"):
            self.repository.create(
                run_id="wrong-target",
                finding=self.finding(),
                sources=self.sources,
                video_version_id=self.video.id,
                observed_artifact_version_id="script-1",
            )
        self.repository.create_script_version(
            version_id="script-2",
            authored_text="Unrelated script.",
            scene=ScriptScene(Environment.NEUTRAL),
        )
        with self.assertRaisesRegex(ValueError, "evidence artifact"):
            self.repository.create(
                run_id="wrong-evidence",
                finding=self.finding(),
                sources=self.sources,
                video_version_id=self.video.id,
                evidence=(
                    EvidenceInput(
                        EvidenceRole.FACT, "script-2", "Unrelated script claim"
                    ),
                ),
            )
        with self.assertRaises(KeyError):
            self.repository.get("wrong-target")
        with self.assertRaises(KeyError):
            self.repository.get("wrong-evidence")

    def test_caption_measurements_reference_persisted_caption_content(self) -> None:
        cues = (
            CaptionCue(
                0, 1000, "A line that is much longer than forty-two characters."
            ),
        )
        self.repository.record_caption_version(
            version_id="caption-1", video_version_id=self.video.id, cues=cues
        )

        run = self.repository.create_caption_quality_run(
            run_id="caption-run", caption_version_id="caption-1"
        )

        assert run is not None
        finding = self.repository.get_quality_finding(run.id)
        records = self.repository.get_quality_evidence(finding.id)
        self.assertEqual(finding.artifact_version_id, "caption-1")
        self.assertEqual(self.repository.get_caption_cues("caption-1"), cues)
        self.assertEqual(records[0].role, EvidenceRole.FACT)
        self.assertEqual(records[0].artifact_version_id, "caption-1")
        self.assertEqual(records[0].limit, "42")
        self.assertEqual(records[-1].role, EvidenceRole.UNCERTAINTY)

    def test_workflow_and_evidence_are_inserted_atomically(self) -> None:
        self.connection.execute(
            """
            CREATE TRIGGER reject_evidence BEFORE INSERT ON quality_evidence
            BEGIN SELECT RAISE(ABORT, 'synthetic evidence failure'); END
            """
        )
        self.connection.commit()

        with self.assertRaisesRegex(
            sqlite3.IntegrityError, "synthetic evidence failure"
        ):
            self.repository.create(
                run_id="run-atomic",
                finding=self.finding(),
                sources=self.sources,
                video_version_id=self.video.id,
            )

        with self.assertRaises(KeyError):
            self.repository.get("run-atomic")
        with self.assertRaises(KeyError):
            self.repository.get_quality_finding("run-atomic")


if __name__ == "__main__":
    unittest.main()
