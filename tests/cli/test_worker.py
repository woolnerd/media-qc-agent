"""Validate worker assembly without waiting for an unbounded poll loop."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from media_qc_agent.cli.worker import _poll, main
from media_qc_agent.workflow.telemetry import stderr_event
from media_qc_agent.workflow.worker_models import WorkerResult


class WorkerCommandTests(unittest.TestCase):
    def test_once_returns_without_poll_sleep(self) -> None:
        with (
            patch("media_qc_agent.cli.worker.DurableWorker") as constructor,
            patch("media_qc_agent.cli.worker.time.sleep") as sleep,
            patch("builtins.print") as output,
        ):
            worker = constructor.return_value
            worker.run_once.return_value = WorkerResult("idle")
            _poll(worker, once=True)
        worker.run_once.assert_called_once_with()
        sleep.assert_not_called()
        output.assert_called_once()

    def test_invalid_limits_do_not_open_database_or_call_provider(self) -> None:
        with (
            patch("sys.argv", ["worker", "--max-attempts", "0"]),
            patch("media_qc_agent.cli.worker.Database") as database,
            patch("media_qc_agent.cli.worker.DurableFakeVideoProvider") as provider,
            patch("sys.stderr"),
            self.assertRaises(SystemExit),
        ):
            main()
        database.assert_not_called()
        provider.assert_not_called()

    def test_events_flag_installs_json_sink_without_changing_poll_output(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "sys.argv",
                [
                    "worker",
                    "--once",
                    "--events",
                    "--database",
                    str(Path(directory) / "workflow.sqlite3"),
                ],
            ),
            patch("media_qc_agent.cli.worker.Database"),
            patch("media_qc_agent.cli.worker.DurableFakeVideoProvider"),
            patch("media_qc_agent.cli.worker.DurableWorker") as constructor,
            patch("builtins.print") as output,
        ):
            constructor.return_value.run_once.return_value = WorkerResult("idle")
            main()
        observer = constructor.call_args.kwargs["observer"]
        self.assertIs(observer.sink, stderr_event)
        output.assert_called_once()
