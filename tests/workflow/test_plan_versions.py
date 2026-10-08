import sqlite3
import unittest
from dataclasses import replace
from unittest.mock import patch

from media_qc_agent import (
    ArtifactKind,
    FailureKind,
    FakeVideoProvider,
    QualityFinding,
    RepairAction,
    WorkflowExecutor,
    WorkflowRepository,
    WorkflowStatus,
)
from media_qc_agent.quality.environment import Environment, ScriptScene
from media_qc_agent.quality.spoken_text import SpokenTextCapabilities
from media_qc_agent.workflow.models import VideoSources, WorkflowRun
from media_qc_agent.workflow.schema import IncompatibleDatabase


class PlanVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.repository = WorkflowRepository(self.connection)
        self.repository.initialize()
        self.sources = VideoSources("script-1", "tts-1", "avatar-1", "voice-1")
        self.repository.artifacts.create_script_version(
            version_id="script-1",
            authored_text="Synthetic sentence.",
            scene=ScriptScene(Environment.NEUTRAL),
        )
        self.repository.artifacts.create_tts_input_version(
            version_id="tts-1",
            script_version_id="script-1",
            capabilities=SpokenTextCapabilities("fake", "literal", frozenset()),
        )
        for version_id in ("avatar-1", "avatar-2", "avatar-3"):
            self.repository.artifacts.create_avatar_version(
                version_id=version_id, environment=Environment.NEUTRAL
            )
        self.repository.artifacts.create_voice_version("voice-1")
        self.video = self.repository.artifacts.create_synthetic_video_version(
            fixture_job_id="observed", sources=self.sources
        )
        self.provider = FakeVideoProvider()

    def tearDown(self) -> None:
        self.connection.close()

    def create_run(
        self, kind: FailureKind = FailureKind.VISUAL_QUALITY, run_id: str = "run-1"
    ) -> WorkflowRun:
        return self.repository.create(
            run_id=run_id,
            finding=QualityFinding(kind, "Synthetic finding", 0.95),
            sources=self.sources,
            observed_artifact_version_id=self.video.id
            if kind is FailureKind.VISUAL_QUALITY
            else None,
        )

    def test_stale_review_cannot_approve_edited_plan(self) -> None:
        original = self.create_run()
        assert original.plan_version_id is not None
        assert original.plan is not None
        edited = self.repository.revise_plan(
            "run-1",
            plan=replace(original.plan, rationale="Reviewed rationale"),
            expected_plan_version_id=original.plan_version_id,
        )
        with self.assertRaisesRegex(ValueError, "version"):
            self.repository.approve("run-1", plan_version_id=original.plan_version_id)
        self.assertEqual(edited.status, WorkflowStatus.AWAITING_APPROVAL)
        self.assertEqual(self.provider.jobs_created, 0)

    def test_edit_between_read_and_submission_creates_no_external_job(self) -> None:
        original = self.create_run()
        self.repository.approve("run-1", plan_version_id=original.plan_version_id)
        get = self.repository.get
        first_read = True

        def read_then_edit(run_id: str) -> WorkflowRun:
            nonlocal first_read
            snapshot = get(run_id)
            if first_read:
                first_read = False
                assert snapshot.plan is not None
                self.repository.revise_plan(
                    run_id,
                    plan=replace(snapshot.plan, rationale="Edited before submission"),
                    expected_plan_version_id=snapshot.plan_version_id,
                )
            return snapshot

        with (
            patch.object(self.repository, "get", side_effect=read_then_edit),
            self.assertRaisesRegex(ValueError, "version"),
        ):
            WorkflowExecutor(repository=self.repository, provider=self.provider).submit(
                "run-1"
            )
        self.assertEqual(self.provider.jobs_created, 0)
        self.assertEqual(get("run-1").status, WorkflowStatus.AWAITING_APPROVAL)

    def test_edit_during_provider_call_is_blocked_and_job_is_recorded(self) -> None:
        original = self.create_run()
        self.repository.approve("run-1", plan_version_id=original.plan_version_id)
        submit = self.provider.submit

        def submit_while_editing(*, idempotency_key: str, action: RepairAction) -> str:
            assert original.plan is not None
            with self.assertRaises(ValueError):
                self.repository.revise_plan(
                    "run-1",
                    plan=replace(original.plan, rationale="Edited during submission"),
                    expected_plan_version_id=original.plan_version_id,
                )
            return submit(idempotency_key=idempotency_key, action=action)

        with patch.object(self.provider, "submit", side_effect=submit_while_editing):
            result = WorkflowExecutor(
                repository=self.repository, provider=self.provider
            ).submit("run-1")
        self.assertEqual(self.provider.jobs_created, 1)
        self.assertEqual(result.status, WorkflowStatus.SUBMITTED)
        assert result.external_job_id is not None
        self.assertEqual(
            self.repository.get_provider_job(result.external_job_id).plan_version_id,
            original.plan_version_id,
        )

    def test_unknown_provider_outcome_keeps_attempt_for_restart(self) -> None:
        original = self.create_run(FailureKind.ENVIRONMENT_MISMATCH)
        self.repository.select_repair("run-1", RepairAction.CHANGE_AVATAR)
        bound = self.repository.bind_replacement("run-1", "avatar-2")
        self.repository.approve("run-1", plan_version_id=bound.plan_version_id)
        submit = self.provider.submit

        def accept_then_timeout(*, idempotency_key: str, action: RepairAction) -> str:
            submit(idempotency_key=idempotency_key, action=action)
            raise TimeoutError("Acceptance response lost")

        with (
            patch.object(self.provider, "submit", side_effect=accept_then_timeout),
            self.assertRaises(TimeoutError),
        ):
            WorkflowExecutor(repository=self.repository, provider=self.provider).submit(
                original.id
            )
        restarted = WorkflowRepository(self.connection)
        reserved = restarted.get(original.id)
        self.assertEqual(reserved.status, WorkflowStatus.SUBMITTING)
        self.assertEqual(reserved.plan_version_id, bound.plan_version_id)
        self.assertEqual(reserved.idempotency_key, bound.idempotency_key)
        with self.assertRaises(ValueError):
            restarted.bind_replacement(original.id, "avatar-3")
        with self.assertRaises(ValueError):
            restarted.request_retry(original.id)
        result = WorkflowExecutor(repository=restarted, provider=self.provider).submit(
            original.id
        )
        self.assertEqual(result.status, WorkflowStatus.SUBMITTED)
        self.assertEqual(self.provider.jobs_created, 1)
        self.assertEqual(self.provider.submit_attempts, 2)

    def test_record_submission_requires_reservation(self) -> None:
        original = self.create_run()
        self.repository.approve(original.id, plan_version_id=original.plan_version_id)
        with self.assertRaisesRegex(ValueError, "reserved"):
            self.repository.record_submission(
                run_id=original.id,
                external_job_id="unreserved-job",
                expected_plan_version_id=original.plan_version_id,
            )
        with self.assertRaises(KeyError):
            self.repository.get_provider_job("unreserved-job")

    def test_edit_invalidates_approval_and_preserves_immutable_history(self) -> None:
        original = self.create_run()
        assert original.plan_version_id is not None
        approved = self.repository.approve(
            "run-1", plan_version_id=original.plan_version_id
        )
        assert original.plan is not None
        edited = self.repository.revise_plan(
            "run-1",
            plan=replace(original.plan, rationale="Updated rationale"),
            expected_plan_version_id=original.plan_version_id,
        )
        self.assertEqual(edited.status, WorkflowStatus.AWAITING_APPROVAL)
        self.assertIsNone(edited.approval)
        with self.assertRaisesRegex(ValueError, "approved"):
            WorkflowExecutor(repository=self.repository, provider=self.provider).submit(
                "run-1"
            )
        versions = self.repository.get_plan_versions("run-1")
        self.assertEqual(versions[0], original.plan_version)
        self.assertEqual(versions[1], edited.plan_version)
        self.assertEqual(
            self.repository.get_plan_approval(original.plan_version_id),
            approved.approval,
        )
        restarted = WorkflowRepository(self.connection)
        self.assertEqual(restarted.get("run-1"), edited)
        self.assertEqual(self.provider.jobs_created, 0)

    def test_clarification_records_exact_replacement_and_reapproval(self) -> None:
        self.create_run(FailureKind.ENVIRONMENT_MISMATCH)
        selected = self.repository.select_repair("run-1", RepairAction.CHANGE_AVATAR)
        bound = self.repository.bind_replacement("run-1", "avatar-2")
        assert bound.plan_version is not None
        assert bound.plan_version_id is not None
        self.assertEqual(bound.plan_version.sources.avatar_version_id, "avatar-2")
        self.assertIn(
            (ArtifactKind.AVATAR, "avatar-2"), bound.plan_version.replacement_choices
        )
        with self.assertRaisesRegex(ValueError, "version"):
            self.repository.approve("run-1", plan_version_id=selected.plan_version_id)
        approved = self.repository.approve(
            "run-1", plan_version_id=bound.plan_version_id
        )
        edited = self.repository.bind_replacement("run-1", "avatar-3")
        self.assertIsNone(edited.approval)
        self.assertEqual(edited.status, WorkflowStatus.AWAITING_APPROVAL)
        self.assertEqual(
            self.repository.get_plan_approval(bound.plan_version_id), approved.approval
        )
        assert edited.plan_version is not None
        self.assertEqual(
            edited.plan_version.replacement_choices,
            ((ArtifactKind.AVATAR, "avatar-3"),),
        )

    def test_each_plan_change_appends_one_current_version_with_its_own_key(
        self,
    ) -> None:
        self.create_run(FailureKind.ENVIRONMENT_MISMATCH)
        runs = [
            self.repository.select_repair("run-1", RepairAction.CHANGE_AVATAR),
            self.repository.bind_replacement("run-1", "avatar-2"),
            self.repository.bind_replacement("run-1", "avatar-3"),
        ]
        versions = self.repository.get_plan_versions("run-1")
        self.assertEqual(len(versions), len(runs))
        for run, version in zip(runs, versions, strict=True):
            self.assertEqual(run.plan_version, version)
            self.assertEqual(run.sources, version.sources)
            self.assertEqual(run.idempotency_key, version.idempotency_key)
        self.assertIsNone(versions[0].idempotency_key)
        keys = [version.idempotency_key for version in versions[1:]]
        self.assertEqual(len(set(keys)), len(keys))

    def test_retry_requires_its_own_version_approval(self) -> None:
        original = self.create_run()
        assert original.plan_version_id is not None
        self.repository.approve("run-1", plan_version_id=original.plan_version_id)
        executor = WorkflowExecutor(repository=self.repository, provider=self.provider)
        executor.submit("run-1")
        retried = self.repository.request_retry("run-1")
        self.assertNotEqual(retried.plan_version_id, original.plan_version_id)
        self.assertIsNone(retried.approval)
        with self.assertRaisesRegex(ValueError, "version"):
            self.repository.approve("run-1", plan_version_id=original.plan_version_id)
        self.assertEqual(self.provider.jobs_created, 1)

    def test_edit_cannot_broaden_policy_or_change_after_submission(self) -> None:
        original = self.create_run()
        assert original.plan_version_id is not None
        assert original.plan is not None
        with self.assertRaisesRegex(ValueError, "policy"):
            self.repository.revise_plan(
                "run-1",
                plan=replace(original.plan, invalidates=frozenset(ArtifactKind)),
                expected_plan_version_id=original.plan_version_id,
            )
        self.repository.approve("run-1", plan_version_id=original.plan_version_id)
        WorkflowExecutor(repository=self.repository, provider=self.provider).submit(
            "run-1"
        )
        with self.assertRaises(ValueError):
            self.repository.revise_plan(
                "run-1",
                plan=original.plan,
                expected_plan_version_id=original.plan_version_id,
            )

    def test_ready_status_alone_cannot_authorize_side_effect(self) -> None:
        original = self.create_run()
        self.connection.execute(
            "UPDATE workflow_runs SET status = ? WHERE id = ?",
            (WorkflowStatus.READY, "run-1"),
        )
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, "approval"):
            WorkflowExecutor(repository=self.repository, provider=self.provider).submit(
                "run-1"
            )
        self.assertEqual(self.provider.jobs_created, 0)
        self.assertIsNone(self.repository.get("run-1").approval)
        self.assertIsNone(original.approval)

    def test_run_cannot_point_at_another_runs_approved_version(self) -> None:
        other = self.create_run(run_id="run-2")
        self.repository.approve("run-2", plan_version_id=other.plan_version_id)
        self.create_run()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "its own plan versions"):
            self.connection.execute(
                "UPDATE workflow_runs SET plan_version_id = ? WHERE id = 'run-1'",
                (other.plan_version_id,),
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "new run cannot point"):
            self.connection.execute(
                """INSERT INTO workflow_runs (
                       id, status, plan_version_id, observed_script_version_id,
                       observed_tts_input_version_id, observed_avatar_version_id,
                       observed_voice_version_id
                   ) VALUES ('run-3', 'ready', ?, 'script-1', 'tts-1', 'avatar-1',
                             'voice-1')""",
                (other.plan_version_id,),
            )

    def test_submission_uses_the_approved_version_not_the_run_row(self) -> None:
        original = self.create_run()
        self.repository.approve("run-1", plan_version_id=original.plan_version_id)
        self.connection.execute(
            "UPDATE workflow_runs SET observed_avatar_version_id = ? WHERE id = ?",
            ("avatar-2", "run-1"),
        )
        self.connection.commit()
        submitted = WorkflowExecutor(
            repository=self.repository, provider=self.provider
        ).submit("run-1")
        assert submitted.external_job_id is not None
        job = self.repository.get_provider_job(submitted.external_job_id)
        assert original.plan_version is not None
        self.assertEqual(job.sources, original.plan_version.sources)
        self.assertEqual(job.plan_version_id, original.plan_version_id)

    def test_plan_and_approval_rows_cannot_be_updated_or_deleted(self) -> None:
        run = self.create_run()
        assert run.plan_version_id is not None
        self.repository.approve("run-1", plan_version_id=run.plan_version_id)
        for table in ("repair_plan_versions", "plan_approvals"):
            for statement in (
                f"DELETE FROM {table}",
                f"UPDATE {table} SET created_at = 'tampered'",
            ):
                with (
                    self.subTest(statement=statement),
                    self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"),
                    self.connection,
                ):
                    self.connection.execute(statement)
        self.assertEqual(len(self.repository.get_plan_versions("run-1")), 1)
        self.assertIsNotNone(self.repository.get("run-1").approval)

    def test_database_from_another_schema_version_is_rejected(self) -> None:
        legacy = sqlite3.connect(":memory:")
        self.addCleanup(legacy.close)
        legacy.execute("CREATE TABLE workflow_runs (id TEXT PRIMARY KEY)")
        with self.assertRaisesRegex(IncompatibleDatabase, "delete this demo database"):
            WorkflowRepository(legacy).initialize()
        self.repository.initialize()

    def test_script_and_tts_choices_both_survive_restart_and_rebinding(self) -> None:
        original = self.create_run(FailureKind.SCRIPT_QUALITY)
        assert original.plan_version_id is not None
        self.repository.artifacts.create_script_version(
            version_id="script-2",
            authored_text="Revised synthetic text.",
            scene=ScriptScene(Environment.NEUTRAL),
        )
        self.repository.artifacts.create_tts_input_version(
            version_id="tts-2",
            script_version_id="script-2",
            capabilities=SpokenTextCapabilities("fake", "literal", frozenset()),
        )
        self.repository.artifacts.create_tts_input_version(
            version_id="tts-3",
            script_version_id="script-2",
            capabilities=SpokenTextCapabilities("fake", "literal", frozenset()),
        )
        self.repository.bind_replacement("run-1", "script-2")
        revised = self.repository.bind_tts_input("run-1", "tts-2")
        assert revised.plan_version is not None
        self.assertEqual(
            revised.plan_version.replacement_choices,
            ((ArtifactKind.SCRIPT, "script-2"), (ArtifactKind.TTS_INPUT, "tts-2")),
        )
        self.repository.approve("run-1", plan_version_id=revised.plan_version_id)
        rebound = self.repository.bind_tts_input("run-1", "tts-3")
        self.assertIsNone(rebound.approval)
        self.assertEqual(rebound.status, WorkflowStatus.AWAITING_APPROVAL)
        self.assertEqual(WorkflowRepository(self.connection).get("run-1"), rebound)
        with self.assertRaisesRegex(ValueError, "version"):
            self.repository.approve("run-1", plan_version_id=revised.plan_version_id)

    def test_submission_cannot_be_recorded_against_a_different_revision(self) -> None:
        original = self.create_run()
        assert original.plan is not None
        assert original.plan_version_id is not None
        self.repository.approve("run-1", plan_version_id=original.plan_version_id)
        edited = self.repository.revise_plan(
            "run-1",
            plan=replace(original.plan, rationale="New rationale"),
            expected_plan_version_id=original.plan_version_id,
        )
        self.repository.approve("run-1", plan_version_id=edited.plan_version_id)
        with self.assertRaisesRegex(ValueError, "version"):
            self.repository.record_submission(
                run_id="run-1",
                external_job_id="stale-job",
                expected_plan_version_id=original.plan_version_id,
            )
        self.assertEqual(self.repository.get("run-1").status, WorkflowStatus.READY)
        with self.assertRaises(KeyError):
            self.repository.get_provider_job("stale-job")
