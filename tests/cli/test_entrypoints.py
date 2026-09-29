"""Exercise relocated commands from outside the repository directory."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class CommandEntrypointTests(unittest.TestCase):
    def run_command(self, module: str, *arguments: str) -> dict[str, object]:
        source = Path(__file__).resolve().parents[2] / "src"
        environment = {**os.environ, "PYTHONPATH": str(source)}
        with TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-m", module, *arguments],
                cwd=directory,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        return json.loads(result.stdout)

    def test_workflow_demo_command(self) -> None:
        result = self.run_command("media_qc_agent.cli.demo")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["old_event"], "stale")
        self.assertEqual(result["new_event"], "applied")

    def test_review_demo_command_is_offline(self) -> None:
        result = self.run_command("media_qc_agent.cli.review")
        self.assertIsNone(result["clarification"])
        finding = result["finding"]
        assert isinstance(finding, dict)
        self.assertEqual(finding["kind"], "visual_quality")

    def test_evaluation_command_finds_repository_dataset(self) -> None:
        result = self.run_command(
            "media_qc_agent.cli.evaluate", "--mask-version-labels"
        )
        self.assertEqual(result["mode"], "fixture")
        self.assertEqual(result["passed"], 15)
        self.assertEqual(result["total"], 15)
