import unittest
from dataclasses import replace

from media_qc_agent import (
    ArtifactKind,
    FakeVideoProvider,
    RepairAction,
    RepairPlan,
    WorkflowExecutor,
    WorkflowStatus,
)
from media_qc_agent.workflow.models import (
    PlanApproval,
    RepairPlanVersion,
    VideoSources,
    WorkflowRun,
    require_video_submission,
)


class InMemoryWorkflowStore:
    def __init__(self, run: WorkflowRun) -> None:
        self.run = run
        self.recorded_submissions = 0

    def get(self, run_id: str) -> WorkflowRun:
        if run_id != self.run.id:
            raise KeyError(run_id)
        return self.run

    def record_submission(
        self,
        *,
        run_id: str,
        external_job_id: str,
        expected_plan_version_id: str | None,
        traceparent: str | None = None,
    ) -> WorkflowRun:
        if run_id != self.run.id:
            raise KeyError(run_id)
        self.recorded_submissions += 1
        self.run = replace(
            self.run,
            status=WorkflowStatus.SUBMITTED,
            external_job_id=external_job_id,
        )
        return self.run

    def reserve_submission(
        self, *, run_id: str, expected_plan_version_id: str | None
    ) -> WorkflowRun:
        run = self.get(run_id)
        if run.plan_version_id != expected_plan_version_id:
            raise ValueError("plan version changed")
        self.run = replace(run, status=WorkflowStatus.SUBMITTING)
        return self.run


def approved_run() -> WorkflowRun:
    run = WorkflowRun(
        id="run-1",
        status=WorkflowStatus.READY,
        plan=RepairPlan(
            action=RepairAction.REGENERATE_VIDEO,
            invalidates=frozenset({ArtifactKind.VIDEO, ArtifactKind.CAPTIONS}),
            requires_repair_input=False,
            rationale="Retry the video",
        ),
        clarification=None,
        idempotency_key="workflow-run:run-1:regenerate_video",
        external_job_id=None,
        sources=VideoSources("script-1", "tts-1", "avatar-1", "voice-1"),
        active_video_version_id=None,
        created_at="2026-09-24T00:00:00.000Z",
        updated_at="2026-09-24T00:00:00.000Z",
    )
    assert run.plan is not None
    snapshot = RepairPlanVersion(
        "plan-run-1-1",
        "run-1",
        1,
        run.plan,
        run.sources,
        None,
        None,
        "video:observed",
        (),
        run.idempotency_key,
        run.created_at,
    )
    run = replace(
        run,
        plan_version=snapshot,
        approval=PlanApproval(snapshot.id, run.created_at),
    )
    return run


class WorkflowExecutorBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow_run = approved_run()
        self.store = InMemoryWorkflowStore(self.workflow_run)
        self.provider = FakeVideoProvider()
        self.executor = WorkflowExecutor(
            repository=self.store,
            provider=self.provider,
        )

    def test_submits_with_store_interface_without_sqlite(self) -> None:
        submitted = self.executor.submit("run-1")

        self.assertEqual(submitted.status, WorkflowStatus.SUBMITTED)
        self.assertEqual(submitted.external_job_id, "video-job-1")
        self.assertEqual(self.store.recorded_submissions, 1)

    def test_approval_gate_does_not_depend_on_sqlite(self) -> None:
        self.store.run = replace(
            self.workflow_run, status=WorkflowStatus.AWAITING_APPROVAL
        )

        with self.assertRaisesRegex(ValueError, "approved"):
            self.executor.submit("run-1")

        self.assertEqual(self.provider.jobs_created, 0)
        self.assertEqual(self.store.recorded_submissions, 0)


if __name__ == "__main__":
    unittest.main()


class SubmissionCheckTests(unittest.TestCase):
    """One check guards both the store's reservation and the executor."""

    def setUp(self) -> None:
        self.approved = approved_run()

    def test_each_rejection_reports_its_reason(self) -> None:
        assert self.approved.plan is not None
        caption = replace(self.approved.plan, action=RepairAction.REPAIR_CAPTIONS)
        cases = (
            (
                replace(self.approved, status=WorkflowStatus.AWAITING_APPROVAL),
                "approved",
            ),
            (replace(self.approved, status=WorkflowStatus.SUCCEEDED), "approved"),
            (replace(self.approved, idempotency_key=None), "executable"),
            (replace(self.approved, plan=caption), "caption repair"),
            (replace(self.approved, approval=None), "plan-version approval"),
        )
        for run, reason in cases:
            with (
                self.subTest(reason=reason),
                self.assertRaisesRegex(ValueError, reason),
            ):
                require_video_submission(run)

    def test_ready_and_reserved_approved_runs_pass(self) -> None:
        for status in (WorkflowStatus.READY, WorkflowStatus.SUBMITTING):
            with self.subTest(status=status):
                require_video_submission(replace(self.approved, status=status))
