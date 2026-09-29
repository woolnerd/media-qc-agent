import unittest
from unittest.mock import patch

from fastapi import FastAPI

from media_qc_agent.cli.serve import main


class ServeCommandTests(unittest.TestCase):
    def test_server_binds_only_to_loopback(self) -> None:
        with (
            patch(
                "sys.argv", ["serve", "--port", "8001", "--database", "demo.sqlite3"]
            ),
            patch("media_qc_agent.cli.serve.uvicorn.run") as serve,
        ):
            main()
        arguments, options = serve.call_args
        self.assertIsInstance(arguments[0], FastAPI)
        self.assertEqual(options, {"host": "127.0.0.1", "port": 8001})

    def test_invalid_port_does_not_start_server(self) -> None:
        with (
            patch("sys.argv", ["serve", "--port", "0"]),
            patch("media_qc_agent.cli.serve.uvicorn.run") as serve,
            patch("sys.stderr"),
            self.assertRaises(SystemExit),
        ):
            main()
        serve.assert_not_called()
