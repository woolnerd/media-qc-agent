"""Open and close SQLite connections in the thread that performs the operation."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from media_qc_agent.workflow.repository import WorkflowRepository


class Database:
    def __init__(self, path: Path) -> None:
        if str(path) == ":memory:":
            raise ValueError(
                "workflow operations require a file database for separate connections"
            )
        self.path = path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def repository(self) -> Iterator[WorkflowRepository]:
        with self.connection() as connection:
            yield WorkflowRepository(connection)
