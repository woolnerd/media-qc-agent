"""Prove edit/reservation ordering with competing SQLite connections."""

import sqlite3
import unittest
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from media_qc_agent import (
    ArtifactKind,
    FailureKind,
    FakeVideoProvider,
    QualityFinding,
    WorkflowExecutor,
    WorkflowRepository,
    WorkflowStatus,
)
from media_qc_agent.environment import Environment, ScriptScene
from media_qc_agent.spoken_text import SpokenTextCapabilities
from media_qc_agent.workflow import VideoSources, WorkflowRun

Operation = Callable[[WorkflowRepository], WorkflowRun]


class SubmissionConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "workflow.sqlite"
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        self.repository = WorkflowRepository(self.connection)
        self.repository.initialize()
        self.sources = VideoSources("script-1", "tts-1", "avatar-1", "voice-1")
        self.repository.create_script_version(
            version_id="script-1",
            authored_text="Synthetic sentence.",
            scene=ScriptScene(Environment.NEUTRAL),
        )
        self.repository.create_tts_input_version(
            version_id="tts-1",
            script_version_id="script-1",
            capabilities=SpokenTextCapabilities("fake", "literal", frozenset()),
        )
        self.repository.create_avatar_version(
            version_id="avatar-1", environment=Environment.NEUTRAL
        )
        self.repository.create_source_version(
            version_id="voice-1", kind=ArtifactKind.VOICE
        )
        video = self.repository.create_synthetic_video_version(
            fixture_job_id="observed", sources=self.sources
        )
        run = self.repository.create(
            run_id="run-1",
            finding=QualityFinding(FailureKind.VISUAL_QUALITY, "Synthetic jump", 0.95),
            sources=self.sources,
            observed_artifact_version_id=video.id,
        )
        self.approved = self.repository.approve(
            run.id, plan_version_id=run.plan_version_id
        )
        self.provider = FakeVideoProvider()

    def _reserve(self, repository: WorkflowRepository) -> WorkflowRun:
        return repository.reserve_submission(
            run_id=self.approved.id,
            expected_plan_version_id=self.approved.plan_version_id,
        )

    def _edit(self, repository: WorkflowRepository) -> WorkflowRun:
        assert self.approved.plan is not None
        return repository.revise_plan(
            self.approved.id,
            plan=replace(self.approved.plan, rationale="Reviewed edit"),
            expected_plan_version_id=self.approved.plan_version_id,
        )

    def _hold_writer(
        self, operation: Operation, locked: Event, release: Event
    ) -> WorkflowRun:
        with closing(sqlite3.connect(self.database, timeout=5)) as connection:
            repository = WorkflowRepository(connection)
            get = repository.get

            def pause_after_read(run_id: str) -> WorkflowRun:
                run = get(run_id)
                if not locked.is_set():
                    # The first read must occur inside BEGIN IMMEDIATE.
                    self.assertTrue(connection.in_transaction)
                    locked.set()
                    if not release.wait(timeout=5):
                        raise TimeoutError("test did not release the SQLite writer")
                return run

            with patch.object(repository, "get", side_effect=pause_after_read):
                return operation(repository)

    def _compete(self, operation: Operation, started: Event) -> WorkflowRun:
        with closing(sqlite3.connect(self.database, timeout=5)) as connection:
            repository = WorkflowRepository(connection)

            def mark_writer_attempt(statement: str) -> None:
                if statement == "BEGIN IMMEDIATE":
                    started.set()

            connection.set_trace_callback(mark_writer_attempt)
            return operation(repository)

    def _race(self, winner: Operation, loser: Operation) -> WorkflowRun:
        locked, release, started = Event(), Event(), Event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self._hold_writer, winner, locked, release)
            try:
                self.assertTrue(
                    locked.wait(timeout=5), "first writer never acquired lock"
                )
                second = pool.submit(self._compete, loser, started)
                self.assertTrue(
                    started.wait(timeout=5), "competing writer never started"
                )
            finally:
                release.set()
            result = first.result(timeout=5)
            # A lock timeout or thread error must fail, not count as rejection.
            with self.assertRaises(ValueError):
                second.result(timeout=5)
        return result

    def test_edit_wins_and_stale_reservation_creates_no_job(self) -> None:
        edited = self._race(self._edit, self._reserve)
        self.assertEqual(edited.status, WorkflowStatus.AWAITING_APPROVAL)
        self.assertNotEqual(edited.plan_version_id, self.approved.plan_version_id)
        self.assertIsNone(edited.approval)
        with self.assertRaisesRegex(ValueError, "approved"):
            WorkflowExecutor(repository=self.repository, provider=self.provider).submit(
                self.approved.id
            )
        self.assertEqual(self.provider.submit_attempts, 0)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM provider_jobs").fetchone()[0],
            0,
        )

    def test_reservation_wins_and_edit_cannot_change_submitted_scope(self) -> None:
        reserved = self._race(self._reserve, self._edit)
        # A separate connection observes the committed reservation.
        self.assertEqual(self.repository.get(self.approved.id), reserved)
        self.assertEqual(reserved.status, WorkflowStatus.SUBMITTING)
        self.assertEqual(reserved.plan_version_id, self.approved.plan_version_id)
        self.assertEqual(reserved.idempotency_key, self.approved.idempotency_key)
        self.assertEqual(len(self.repository.get_plan_versions(self.approved.id)), 1)
        submitted = WorkflowExecutor(
            repository=self.repository, provider=self.provider
        ).submit(self.approved.id)
        self.assertEqual(submitted.status, WorkflowStatus.SUBMITTED)
        assert submitted.external_job_id is not None
        job = self.repository.get_provider_job(submitted.external_job_id)
        self.assertEqual(job.plan_version_id, self.approved.plan_version_id)
        self.assertEqual(job.sources, self.approved.sources)
        self.assertEqual(job.idempotency_key, self.approved.idempotency_key)
        self.assertEqual(self.provider.jobs_created, 1)
