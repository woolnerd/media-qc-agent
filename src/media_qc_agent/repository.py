"""SQLite persistence for workflow runs and state transitions."""

import json
import sqlite3

from .domain import (
    ArtifactKind,
    ClarificationRequest,
    QualityFinding,
    RepairAction,
    RepairOption,
    RepairPlan,
)
from .planner import plan_repair, select_repair
from .workflow import WorkflowRun, WorkflowStatus


class WorkflowRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def initialize(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_runs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                action TEXT,
                invalidates TEXT NOT NULL,
                requires_repair_input INTEGER NOT NULL,
                rationale TEXT NOT NULL,
                clarification_question TEXT,
                clarification_options TEXT,
                idempotency_key TEXT UNIQUE,
                external_job_id TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """
        )
        self._connection.commit()

    def create(self, *, run_id: str, finding: QualityFinding) -> WorkflowRun:
        decision = plan_repair(finding)
        if isinstance(decision, ClarificationRequest):
            plan = None
            clarification = decision
            initial_status = WorkflowStatus.NEEDS_INPUT
        else:
            plan = decision
            clarification = None
            initial_status = (
                WorkflowStatus.NEEDS_INPUT
                if plan.requires_repair_input
                else WorkflowStatus.AWAITING_APPROVAL
            )

        self._connection.execute(
            """
            INSERT INTO workflow_runs (
                id,
                status,
                action,
                invalidates,
                requires_repair_input,
                rationale,
                clarification_question,
                clarification_options,
                idempotency_key,
                external_job_id
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                NULL
            )
            """,
            (
                run_id,
                initial_status,
                plan.action if plan else None,
                _encode_invalidates(plan.invalidates) if plan else "",
                plan.requires_repair_input if plan else False,
                plan.rationale if plan else "",
                clarification.question if clarification else None,
                _encode_options(clarification.options) if clarification else None,
                f"workflow-run:{run_id}:{plan.action}" if plan else None,
            ),
        )
        self._connection.commit()
        return self.get(run_id)

    def select_repair(self, run_id: str, action: RepairAction) -> WorkflowRun:
        """Record a branch choice while awaiting its replacement artifact."""

        current = self.get(run_id)
        if (
            current.status is not WorkflowStatus.NEEDS_INPUT
            or current.clarification is None
        ):
            raise ValueError("workflow is not awaiting clarification")
        plan = select_repair(current.clarification, action)
        result = self._connection.execute(
            """
            UPDATE workflow_runs
            SET status = ?, action = ?, invalidates = ?,
                requires_repair_input = ?, rationale = ?,
                clarification_question = NULL, clarification_options = NULL,
                idempotency_key = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ? AND status = ? AND action IS NULL
            """,
            (
                WorkflowStatus.NEEDS_REPAIR_INPUT,
                plan.action,
                _encode_invalidates(plan.invalidates),
                plan.requires_repair_input,
                plan.rationale,
                None,
                run_id,
                WorkflowStatus.NEEDS_INPUT,
            ),
        )
        self._connection.commit()
        if result.rowcount != 1:
            raise RuntimeError("workflow state changed during clarification")
        return self.get(run_id)

    def approve(self, run_id: str) -> WorkflowRun:
        result = self._connection.execute(
            """
            UPDATE workflow_runs
            SET status = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ? AND status = ?
            """,
            (
                WorkflowStatus.READY,
                run_id,
                WorkflowStatus.AWAITING_APPROVAL,
            ),
        )
        self._connection.commit()
        if result.rowcount != 1:
            raise ValueError("workflow is missing or is not awaiting approval")
        return self.get(run_id)

    def record_submission(self, *, run_id: str, external_job_id: str) -> WorkflowRun:
        """Record an accepted provider job without permitting ID replacement."""

        current = self.get(run_id)
        if current.status is WorkflowStatus.SUBMITTED:
            if current.external_job_id != external_job_id:
                raise ValueError("workflow already references another provider job")
            return current
        if current.status is not WorkflowStatus.READY:
            raise ValueError("workflow is not ready for provider submission")

        result = self._connection.execute(
            """
            UPDATE workflow_runs
            SET status = ?, external_job_id = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ? AND status = ?
            """,
            (
                WorkflowStatus.SUBMITTED,
                external_job_id,
                run_id,
                WorkflowStatus.READY,
            ),
        )
        self._connection.commit()
        if result.rowcount != 1:
            raise RuntimeError("workflow state changed during provider submission")
        return self.get(run_id)

    def get(self, run_id: str) -> WorkflowRun:
        row = self._connection.execute(
            "SELECT * FROM workflow_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)

        invalidates = frozenset(
            ArtifactKind(value) for value in row["invalidates"].split(",") if value
        )
        clarification = None
        if row["clarification_question"] is not None:
            clarification = ClarificationRequest(
                question=row["clarification_question"],
                options=_decode_options(row["clarification_options"]),
            )
        return WorkflowRun(
            id=row["id"],
            status=WorkflowStatus(row["status"]),
            plan=(
                RepairPlan(
                    action=RepairAction(row["action"]),
                    invalidates=invalidates,
                    requires_repair_input=bool(row["requires_repair_input"]),
                    rationale=row["rationale"],
                )
                if row["action"] is not None
                else None
            ),
            clarification=clarification,
            idempotency_key=row["idempotency_key"],
            external_job_id=row["external_job_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _encode_invalidates(invalidates: frozenset[ArtifactKind]) -> str:
    return ",".join(sorted(artifact.value for artifact in invalidates))


def _encode_options(options: tuple[RepairOption, ...]) -> str:
    return json.dumps(
        [
            {
                "action": option.action.value,
                "invalidates": sorted(
                    artifact.value for artifact in option.invalidates
                ),
                "rationale": option.rationale,
            }
            for option in options
        ]
    )


def _decode_options(value: str) -> tuple[RepairOption, ...]:
    return tuple(
        RepairOption(
            action=RepairAction(option["action"]),
            invalidates=frozenset(
                ArtifactKind(artifact) for artifact in option["invalidates"]
            ),
            rationale=option["rationale"],
        )
        for option in json.loads(value)
    )
