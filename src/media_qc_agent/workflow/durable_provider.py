"""A separate SQLite store simulates provider-owned state across process restarts."""

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path


def provider_ledger_path(workflow_path: Path) -> Path:
    canonical = workflow_path.resolve()
    return canonical.with_name(canonical.name + ".provider.sqlite3")


class DurableFakeVideoProvider:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS fake_provider_jobs (
                     idempotency_key TEXT PRIMARY KEY, action TEXT NOT NULL,
                     external_job_id TEXT NOT NULL UNIQUE)"""
            )

    def submit(self, *, idempotency_key: str, action: str) -> str:
        external_job_id = (
            "fake-job-" + hashlib.sha256(idempotency_key.encode()).hexdigest()
        )
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT OR IGNORE INTO fake_provider_jobs VALUES (?, ?, ?)",
                    (idempotency_key, action, external_job_id),
                )
                row = connection.execute(
                    "SELECT action, external_job_id FROM fake_provider_jobs WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if row[0] != action:
                    raise ValueError(
                        "idempotency key was reused for a different action"
                    )
                return str(row[1])
        finally:
            connection.close()

    @property
    def jobs_created(self) -> int:
        connection = sqlite3.connect(self.path)
        try:
            return int(
                connection.execute(
                    "SELECT count(*) FROM fake_provider_jobs"
                ).fetchone()[0]
            )
        finally:
            connection.close()

    def job_for_key(self, key: str) -> str | None:
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                "SELECT external_job_id FROM fake_provider_jobs WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
        return str(row[0]) if row else None
