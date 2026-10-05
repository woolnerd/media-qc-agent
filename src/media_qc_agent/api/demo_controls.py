"""Explicit synthetic fault harness; ordinary approval routes never submit work."""

import time
from collections.abc import Callable
from dataclasses import replace
from typing import Annotated, Literal

from pydantic import StringConstraints

from media_qc_agent.api.review_data import ReviewSnapshot, review_snapshot
from media_qc_agent.api.schemas import Identifier, RequestBody
from media_qc_agent.domain.models import RepairAction
from media_qc_agent.quality.captions import CaptionCue
from media_qc_agent.workflow.database import Database
from media_qc_agent.workflow.durable_provider import (
    DurableFakeVideoProvider,
    provider_ledger_path,
)
from media_qc_agent.workflow.executor import SimulatedProcessCrash
from media_qc_agent.workflow.models import WorkflowRun, WorkflowStatus
from media_qc_agent.workflow.repository import WorkflowRepository
from media_qc_agent.workflow.worker import DurableWorker
from media_qc_agent.workflow.worker_queue import load_worker_policy

UiAction = Literal[
    "approve",
    "choose",
    "bind",
    "tts",
    "edit",
    "captions",
    "retry",
    "submit",
    "interrupt",
    "recover",
    "complete",
    "duplicate",
]


class UiCommand(RequestBody):
    action: UiAction
    expected_plan_version_id: Identifier | None = None
    value: Annotated[str, StringConstraints(strict=True, max_length=4000)] = ""


class DemoControls:
    def __init__(
        self, database: Database, clock: Callable[[], float] = time.time
    ) -> None:
        self.database = database
        self.clock = clock
        self._provider: DurableFakeVideoProvider | None = None

    def initialize(self) -> None:
        self._provider = DurableFakeVideoProvider(
            provider_ledger_path(self.database.path)
        )

    @property
    def provider(self) -> DurableFakeVideoProvider:
        if self._provider is None:
            raise RuntimeError(
                "demo controls must initialize during application startup"
            )
        return self._provider

    def snapshot(self, run_id: str) -> ReviewSnapshot:
        with self.database.connection() as connection, connection:
            repository = WorkflowRepository(connection)
            connection.execute("BEGIN")
            run = repository.get(run_id)
            accepted = (
                self.provider.job_for_key(run.idempotency_key)
                if run.idempotency_key
                else None
            )
            return review_snapshot(
                connection, repository, run_id, self.clock(), accepted
            )

    def execute(self, run_id: str, command: UiCommand) -> str:
        with self.database.repository() as repository:
            run = repository.get(run_id)
            if run.plan_version_id != command.expected_plan_version_id:
                raise ValueError(
                    "The plan changed. Refresh and review the current version."
                )
            if command.action in {"submit", "interrupt", "recover"}:
                return self._step(run, command)
            self._command(repository, run, command)
        return {
            "approve": "Exact plan approved. Execute its approved repair below.",
            "captions": "Caption repair accepted. The existing video was preserved.",
            "complete": "Completion audited. Inspect the current replacement and lineage below.",
            "duplicate": "Same completion replayed. No second replacement version was created.",
            "retry": "New attempt requested. Review and approve its new plan version.",
        }.get(command.action, "Change saved. Review the current plan before approval.")

    def _command(
        self, repository: WorkflowRepository, run: WorkflowRun, command: UiCommand
    ) -> None:
        operations: dict[str, Callable[[], object]] = {
            "approve": lambda: repository.approve(
                run.id, plan_version_id=command.expected_plan_version_id
            ),
            "choose": lambda: repository.select_repair(
                run.id, RepairAction(command.value)
            ),
            "bind": lambda: repository.bind_replacement(
                run.id,
                command.value,
                expected_plan_version_id=command.expected_plan_version_id,
            ),
            "tts": lambda: repository.bind_tts_input(
                run.id,
                command.value,
                expected_plan_version_id=command.expected_plan_version_id,
            ),
            "retry": lambda: repository.request_retry(
                run.id, expected_plan_version_id=command.expected_plan_version_id
            ),
            "edit": lambda: self._edit(repository, run, command),
            "captions": lambda: self._captions(repository, run, command),
            "complete": lambda: self._completion(repository, run, command),
            "duplicate": lambda: self._completion(repository, run, command),
        }
        operations[command.action]()

    def _edit(
        self, repository: WorkflowRepository, run: WorkflowRun, command: UiCommand
    ) -> None:
        if run.plan is None or not command.value.strip():
            raise ValueError("A plan and a nonblank rationale are required.")
        repository.revise_plan(
            run.id,
            plan=replace(run.plan, rationale=command.value),
            expected_plan_version_id=command.expected_plan_version_id,
        )

    def _captions(
        self, repository: WorkflowRepository, run: WorkflowRun, command: UiCommand
    ) -> None:
        repository.record_caption_repair(
            run_id=run.id,
            version_id=f"caption-{run.id}-repair-{run.plan_version_id}",
            cues=(CaptionCue(0, 1000, command.value),),
            expected_plan_version_id=command.expected_plan_version_id,
        )

    def _completion(
        self, repository: WorkflowRepository, run: WorkflowRun, command: UiCommand
    ) -> None:
        job = repository.get_provider_job(command.value)
        if job.run_id != run.id:
            raise ValueError("The provider job belongs to a different run.")
        event_id = "demo-completion:" + job.external_job_id
        if command.action == "duplicate":
            events = repository.list_provider_events(run.id)
            recorded = next(
                (
                    event
                    for event in events
                    if event.external_job_id == job.external_job_id
                ),
                None,
            )
            if recorded is None:
                raise ValueError("Complete the job before replaying its callback.")
            event_id = recorded.external_event_id
        repository.record_completion(
            external_job_id=job.external_job_id, external_event_id=event_id
        )

    def _step(self, run: WorkflowRun, command: UiCommand) -> str:
        expected_status = (
            WorkflowStatus.SUBMITTING
            if command.action == "recover"
            else WorkflowStatus.READY
        )
        if run.status is not expected_status:
            raise ValueError(
                "This worker control is not available in the current state."
            )
        with self.database.connection() as connection:
            WorkflowRepository(connection)
            policy = load_worker_policy(connection)
        worker = DurableWorker(
            self.database,
            self.provider,
            owner="review-demo",
            policy=policy,
            clock=self.clock,
            observer=self.database.observer,
        )
        try:
            result = worker.run_once(
                run_id=run.id,
                expected_plan_version_id=command.expected_plan_version_id,
                crash_after_provider_accepts=command.action == "interrupt",
            )
        except SimulatedProcessCrash:
            return "Interrupted after synthetic provider acceptance. Wait for lease expiry, then recover."
        return {
            "submitted": "Worker recorded the accepted job. Complete it to create the replacement.",
            "idle": "No claim made. Check lease, backoff, approval, and shared capacity; then refresh.",
            "lease_lost": "Worker ownership expired. Refresh to inspect the current owner and state.",
        }.get(result.outcome, "Worker result: " + result.outcome)
