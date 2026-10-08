import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from media_qc_agent.workflow.schema import (
    SCHEMA_VERSION,
    IncompatibleDatabase,
    initialize_schema,
)


class SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "workflow.sqlite3"

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        self.addCleanup(connection.close)
        return connection

    def test_initialization_is_idempotent_and_versioned(self) -> None:
        initialize_schema(self.connect())
        connection = self.connect()
        initialize_schema(connection)
        self.assertEqual(
            connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION
        )

    def test_concurrent_first_initialization_creates_one_schema(self) -> None:
        barrier = threading.Barrier(4)
        errors: list[Exception] = []

        def initialize() -> None:
            connection = sqlite3.connect(self.path, timeout=10)
            try:
                barrier.wait(timeout=5)
                initialize_schema(connection)
            except (
                sqlite3.Error,
                IncompatibleDatabase,
                threading.BrokenBarrierError,
            ) as error:
                errors.append(error)
            finally:
                connection.close()

        threads = [threading.Thread(target=initialize) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])

    def test_losing_the_creation_race_accepts_the_winner_schema(self) -> None:
        initialize_schema(self.connect())
        stale_read = [(0, False), (SCHEMA_VERSION, True)]
        with patch("media_qc_agent.workflow.schema._state", side_effect=stale_read):
            initialize_schema(self.connect())

    def test_unversioned_or_other_version_database_is_rejected(self) -> None:
        connection = self.connect()
        connection.execute("CREATE TABLE workflow_runs (id TEXT PRIMARY KEY)")
        connection.commit()
        with self.assertRaises(IncompatibleDatabase):
            initialize_schema(connection)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        with self.assertRaisesRegex(IncompatibleDatabase, "delete this demo database"):
            initialize_schema(connection)


if __name__ == "__main__":
    unittest.main()
