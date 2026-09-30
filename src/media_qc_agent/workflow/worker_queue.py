"""SQLite claims, shared capacity, and bounded recovery attempts."""

import sqlite3
import time
from collections.abc import Callable
from dataclasses import astuple
from typing import TYPE_CHECKING

from media_qc_agent.domain.ids import validate_run_id
from media_qc_agent.workflow.models import has_current_approval
from media_qc_agent.workflow.worker_models import (
    LeaseLost,
    SubmissionLease,
    WorkerPolicy,
)

if TYPE_CHECKING:
    from media_qc_agent.workflow.repository import WorkflowRepository


def initialize_worker_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS worker_policy (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            max_in_flight INTEGER NOT NULL,
            max_attempts INTEGER NOT NULL,
            lease_seconds REAL NOT NULL,
            backoff_seconds REAL NOT NULL,
            max_backoff_seconds REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS worker_attempts (
            plan_version_id TEXT PRIMARY KEY REFERENCES repair_plan_versions(id),
            run_id TEXT NOT NULL REFERENCES workflow_runs(id),
            attempts INTEGER NOT NULL,
            lease_owner TEXT,
            lease_until REAL,
            next_attempt_at REAL NOT NULL DEFAULT 0,
            stopped INTEGER NOT NULL DEFAULT 0,
            error_type TEXT
        );
        """
    )


def require_lease(
    connection: sqlite3.Connection, lease: SubmissionLease | None, now: float
) -> None:
    if lease is None:
        return
    row = connection.execute(
        """SELECT 1 FROM worker_attempts WHERE plan_version_id = ? AND run_id = ?
           AND lease_owner = ? AND attempts = ? AND lease_until > ?""",
        (lease.plan_version_id, lease.run_id, lease.owner, lease.attempt, now),
    ).fetchone()
    if row is None:
        raise LeaseLost("submission lease expired or belongs to another attempt")


def require_submission_lease(
    connection: sqlite3.Connection,
    lease: SubmissionLease | None,
    now: float,
    run_id: str,
    plan_version_id: str | None,
) -> None:
    if lease is not None and (lease.run_id, lease.plan_version_id) != (
        run_id,
        plan_version_id,
    ):
        raise LeaseLost("submission lease is bound to a different run or plan")
    require_lease(connection, lease, now)


class SubmissionQueue:
    def __init__(
        self,
        connection: sqlite3.Connection,
        repository: "WorkflowRepository",
        policy: WorkerPolicy,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.connection = connection
        self.repository = repository
        self.policy = policy
        self.clock = clock
        self._configure()

    def _configure(self) -> None:
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                "INSERT OR IGNORE INTO worker_policy VALUES (1, ?, ?, ?, ?, ?)",
                astuple(self.policy),
            )
            row = self.connection.execute(
                "SELECT * FROM worker_policy WHERE id = 1"
            ).fetchone()
            if tuple(row)[1:] != astuple(self.policy):
                raise ValueError(
                    "workers sharing a database must use its persisted policy"
                )

    def claim(self, owner: str) -> SubmissionLease | None:
        validate_run_id(owner)
        now = self.clock()
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self._candidate(now)
            if row is None:
                return None
            current = self.repository.get(row["run_id"])
            if not has_current_approval(current):
                raise ValueError("queued plan does not match its approval")
            lease = SubmissionLease(
                current.id,
                row["plan_version_id"],
                owner,
                row["attempts"] + 1,
                now + self.policy.lease_seconds,
            )
            self._record_claim(lease)
            self.connection.execute(
                """UPDATE workflow_runs SET status = 'submitting',
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?""",
                (current.id,),
            )
            return lease

    def _candidate(self, now: float) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT r.id AS run_id, p.id AS plan_version_id, coalesce(w.attempts, 0) AS attempts
            FROM workflow_runs r
            JOIN repair_plan_versions p ON p.run_id = r.id
              AND p.revision = (SELECT max(revision) FROM repair_plan_versions WHERE run_id = r.id)
            JOIN plan_approvals a ON a.plan_version_id = p.id
            LEFT JOIN worker_attempts w ON w.plan_version_id = p.id
            WHERE r.status IN ('ready', 'submitting') AND r.action <> 'repair_captions'
              AND coalesce(w.stopped, 0) = 0 AND coalesce(w.attempts, 0) < ?
              AND coalesce(w.next_attempt_at, 0) <= ?
              AND (w.lease_until IS NULL OR w.lease_until <= ?)
              AND (r.status = 'submitting' OR
                ((SELECT count(*) FROM provider_jobs j WHERE NOT EXISTS
                   (SELECT 1 FROM provider_events e WHERE e.external_job_id = j.external_job_id))
                 + (SELECT count(*) FROM workflow_runs pending WHERE pending.status = 'submitting'
                    AND NOT EXISTS (SELECT 1 FROM provider_jobs j
                      WHERE j.idempotency_key = pending.idempotency_key))) < ?)
            ORDER BY CASE r.status WHEN 'submitting' THEN 0 ELSE 1 END, r.created_at, r.id
            LIMIT 1
            """,
            (self.policy.max_attempts, now, now, self.policy.max_in_flight),
        ).fetchone()

    def _record_claim(self, lease: SubmissionLease) -> None:
        self.connection.execute(
            """INSERT INTO worker_attempts
                 (plan_version_id, run_id, attempts, lease_owner, lease_until)
               VALUES (?, ?, ?, ?, ?) ON CONFLICT(plan_version_id) DO UPDATE SET
                 attempts = excluded.attempts, lease_owner = excluded.lease_owner,
                 lease_until = excluded.lease_until, error_type = NULL""",
            (
                lease.plan_version_id,
                lease.run_id,
                lease.attempt,
                lease.owner,
                lease.expires_at,
            ),
        )

    def finish(self, lease: SubmissionLease) -> None:
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            require_lease(self.connection, lease, self.clock())
            self.connection.execute(
                "UPDATE worker_attempts SET lease_owner = NULL, lease_until = NULL WHERE plan_version_id = ?",
                (lease.plan_version_id,),
            )

    def fail(self, lease: SubmissionLease, error: Exception, *, retryable: bool) -> str:
        now = self.clock()
        stopped = not retryable or lease.attempt >= self.policy.max_attempts
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            require_lease(self.connection, lease, now)
            self.connection.execute(
                """UPDATE worker_attempts SET lease_owner = NULL, lease_until = NULL,
                   next_attempt_at = ?, stopped = ?, error_type = ? WHERE plan_version_id = ?""",
                (
                    now + self.policy.retry_delay(lease.attempt),
                    stopped,
                    type(error).__name__,
                    lease.plan_version_id,
                ),
            )
        return "stopped" if stopped else "retry_wait"
