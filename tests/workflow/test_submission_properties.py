"""Drive workers, faults, completions, and retries in random orders.

Hypothesis searches for a sequence of steps that breaks a guarantee and
shrinks any failure to the shortest such sequence. `specs/submission.qnt`
models the same protocol; this machine checks the real code.
"""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
)

from media_qc_agent.api.scenarios import create_scenario_run, seed_scenarios
from media_qc_agent.domain.ids import video_version_id
from media_qc_agent.workflow.database import Database
from media_qc_agent.workflow.durable_provider import DurableFakeVideoProvider
from media_qc_agent.workflow.executor import SimulatedProcessCrash
from media_qc_agent.workflow.models import (
    ProviderEventDisposition,
    WorkflowStatus,
    has_current_approval,
)
from media_qc_agent.workflow.worker import DurableWorker
from media_qc_agent.workflow.worker_models import WorkerPolicy, WorkerResult
from media_qc_agent.workflow.worker_queue import outstanding_capacity

OWNERS = ("worker-a", "worker-b")
FAULTS = (
    "none",
    "timeout_before",
    "timeout_after",
    "crash_after",
    "expire",
    "overlap",
    "overlap_crash",
)
OUTLIVED = {"expire", "overlap", "overlap_crash"}
RECOVERY_FAULT = {"overlap": "none", "overlap_crash": "crash_after"}
POLICY = WorkerPolicy(
    max_in_flight=2,
    max_attempts=3,
    lease_seconds=30,
    backoff_seconds=2,
    max_backoff_seconds=4,
)
MAX_RUNS = 3


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FaultyProvider:
    """The durable fake, with one injected fault around its side effect.

    An `expire` call outlives its lease. In an `overlap`, a restarted worker
    with the same owner name also recovers the run meanwhile, the known limit
    in the README. In an `overlap_crash` that recovery crashes holding the
    lease, so only the attempt token tells the two owners apart.
    """

    def __init__(self, inner: DurableFakeVideoProvider, machine: "SubmissionMachine"):
        self.inner = inner
        self.machine = machine
        self.fault = "none"

    def submit(self, *, idempotency_key: str, action: str) -> str:
        fault, self.fault = self.fault, "none"
        if fault == "timeout_before":
            raise TimeoutError("synthetic timeout before acceptance")
        job_id = self.inner.submit(idempotency_key=idempotency_key, action=action)
        if fault in OUTLIVED:
            self.machine.clock.now += POLICY.lease_seconds
            self.machine.outlived_lease = True
        if fault in RECOVERY_FAULT:
            run_id = idempotency_key.split(":")[1]
            self.machine.run_worker(
                self.machine.owner, RECOVERY_FAULT[fault], run_id=run_id
            )
        if fault == "timeout_after":
            raise TimeoutError("synthetic timeout after acceptance")
        return job_id


class SubmissionMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / "workflow.sqlite3"
        self.database = Database(path)
        self.ledger = DurableFakeVideoProvider(
            path.with_name(path.name + ".provider.sqlite3")
        )
        self.provider = FaultyProvider(self.ledger, self)
        self.clock = Clock()
        self.runs: list[str] = []
        self.outlived_lease = False
        self.owner = OWNERS[0]
        with self.database.repository() as repository:
            repository.initialize()
            seed_scenarios(repository)

    def teardown(self) -> None:
        self.directory.cleanup()

    def run_worker(
        self, owner: str, fault: str, *, run_id: str | None = None
    ) -> WorkerResult | None:
        self.provider.fault = "none" if fault == "crash_after" else fault
        worker = DurableWorker(
            self.database, self.provider, owner=owner, policy=POLICY, clock=self.clock
        )
        try:
            return worker.run_once(
                crash_after_provider_accepts=fault == "crash_after", run_id=run_id
            )
        except SimulatedProcessCrash:
            return None
        finally:
            self.provider.fault = "none"

    def recorded_jobs(self) -> list[str]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT external_job_id FROM provider_jobs ORDER BY external_job_id"
            ).fetchall()
        return [row[0] for row in rows]

    def retryable_runs(self) -> list[str]:
        retryable = {WorkflowStatus.SUBMITTED, WorkflowStatus.SUCCEEDED}
        with self.database.repository() as repository:
            return [r for r in self.runs if repository.get(r).status in retryable]

    # Steps

    @precondition(lambda self: len(self.runs) < MAX_RUNS)
    @rule()
    def create_approved_run(self) -> None:
        run_id = f"run-{len(self.runs) + 1}"
        with self.database.repository() as repository:
            run = create_scenario_run(repository, "jerky-video", run_id)
            repository.approve(run_id, plan_version_id=run.plan_version_id)
        self.runs.append(run_id)

    @rule(owner=st.sampled_from(OWNERS), fault=st.sampled_from(FAULTS))
    def work(self, owner: str, fault: str) -> None:
        self.outlived_lease = False
        self.owner = owner
        result = self.run_worker(owner, fault)
        if self.outlived_lease:
            # INV-08: an owner whose lease expired during the call cannot commit.
            assert result is not None and result.outcome == "lease_lost", result

    @rule(seconds=st.sampled_from([1.0, 2.0, POLICY.lease_seconds]))
    def advance_time(self, seconds: float) -> None:
        self.clock.now += seconds

    @precondition(lambda self: bool(self.recorded_jobs()))
    @rule(data=st.data(), delivery=st.integers(0, 1))
    def complete(self, data: st.DataObject, delivery: int) -> None:
        job_id = data.draw(st.sampled_from(self.recorded_jobs()))
        with self.database.repository() as repository:
            repository.record_completion(
                external_job_id=job_id, external_event_id=f"event-{delivery}-{job_id}"
            )

    @precondition(lambda self: bool(self.retryable_runs()))
    @rule(data=st.data())
    def retry_and_approve(self, data: st.DataObject) -> None:
        run_id = data.draw(st.sampled_from(self.retryable_runs()))
        with self.database.repository() as repository:
            current = repository.get(run_id)
            retried = repository.request_retry(
                run_id, expected_plan_version_id=current.plan_version_id
            )
            repository.approve(run_id, plan_version_id=retried.plan_version_id)

    # Guarantees

    @invariant()
    def submission_requires_current_approval(self) -> None:
        """INV-03: work in flight is bound to its approved plan version."""

        in_flight = {WorkflowStatus.SUBMITTING, WorkflowStatus.SUBMITTED}
        with self.database.repository() as repository:
            for run_id in self.runs:
                run = repository.get(run_id)
                if run.status in in_flight:
                    assert has_current_approval(run), run_id

    @invariant()
    def provider_jobs_follow_approved_keys(self) -> None:
        """INV-04 and INV-08: one provider job per approved key, recorded as such."""

        with closing(sqlite3.connect(self.ledger.path)) as ledger:
            keys = [
                row[0]
                for row in ledger.execute(
                    "SELECT idempotency_key FROM fake_provider_jobs"
                )
            ]
        with self.database.repository() as repository:
            for key in keys:
                version_id = key.split(":plan:", 1)[1]
                assert repository.get_plan_approval(version_id) is not None, key
            for run_id in self.runs:
                for job in repository.list_provider_jobs(run_id):
                    assert self.ledger.job_for_key(job.idempotency_key) == (
                        job.external_job_id
                    )

    @invariant()
    def only_applied_completions_create_videos(self) -> None:
        """INV-05: one video per applied completion, from its job's exact inputs."""

        with self.database.repository() as repository:
            for run_id in self.runs:
                run = repository.get(run_id)
                videos = repository.generated_videos(run_id)
                applied = [
                    event
                    for event in repository.list_provider_events(run_id)
                    if event.disposition is ProviderEventDisposition.APPLIED
                ]
                assert len(videos) == len(applied) == len({v.id for v in videos})
                for video in videos:
                    assert video.external_job_id is not None
                    job = repository.get_provider_job(video.external_job_id)
                    expected = job.sources.dependencies()
                    assert len(video.source_versions) == len(expected), video.id
                    assert set(video.source_versions) == set(expected), video.id
                if run.status is WorkflowStatus.SUCCEEDED:
                    assert run.external_job_id is not None
                    assert run.active_video_version_id == video_version_id(
                        run.external_job_id
                    )

    def expected_capacity(self) -> int:
        """Outstanding jobs plus uncertain reservations, counted from the runs.

        Derived from repository reads rather than `outstanding_capacity`, so an
        undercount in the helper that admits excess work cannot certify itself.
        A job stays outstanding until the provider reports any event for it.
        """

        slots = 0
        with self.database.repository() as repository:
            for run_id in self.runs:
                run = repository.get(run_id)
                reported = {
                    e.external_job_id for e in repository.list_provider_events(run_id)
                }
                jobs = repository.list_provider_jobs(run_id)
                slots += sum(job.external_job_id not in reported for job in jobs)
                uncertain = run.status is WorkflowStatus.SUBMITTING and not any(
                    job.plan_version_id == run.plan_version_id for job in jobs
                )
                slots += uncertain
        return slots

    @invariant()
    def capacity_and_attempts_stay_bounded(self) -> None:
        """INV-08 and INV-09: attempts stay in budget; slots stay counted."""

        expected = self.expected_capacity()
        assert expected <= POLICY.max_in_flight, expected
        with self.database.connection() as connection:
            assert outstanding_capacity(connection) == expected
            most = connection.execute(
                "SELECT coalesce(max(attempts), 0) FROM worker_attempts"
            ).fetchone()[0]
        assert most <= POLICY.max_attempts


SubmissionMachine.TestCase.settings = settings(
    max_examples=200, stateful_step_count=30, deadline=None
)


class SubmissionPropertyTests(SubmissionMachine.TestCase):  # type: ignore[misc,valid-type]
    pass


if __name__ == "__main__":
    unittest.main()
