"""Open and close SQLite connections inside each synchronous request thread."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from media_qc_agent.workflow.repository import WorkflowRepository


class Database:
    def __init__(self, path: Path) -> None:
        if str(path) == ":memory:":
            raise ValueError("the API requires a file database for request connections")
        self.path = path

    @contextmanager
    def repository(self) -> Iterator[WorkflowRepository]:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield WorkflowRepository(connection)
        finally:
            connection.close()
