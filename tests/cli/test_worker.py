"""Validate worker assembly without waiting for an unbounded poll loop."""

import unittest
from unittest.mock import patch

from media_qc_agent.cli.worker import _poll, main
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
