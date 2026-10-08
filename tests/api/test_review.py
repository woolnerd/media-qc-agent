import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from media_qc_agent.api.app import create_app
from media_qc_agent.api.demo_controls import DemoControls, UiCommand
from media_qc_agent.workflow.database import Database
from media_qc_agent.workflow.durable_provider import (
    DurableFakeVideoProvider,
    provider_ledger_path,
)
from media_qc_agent.workflow.models import WorkflowRun
from media_qc_agent.workflow.repository import WorkflowRepository


class ReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "review.sqlite3"
        self.now = 1000.0
        self.client = TestClient(create_app(self.path, clock=lambda: self.now))
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def create(self, scenario: str = "jerky-video", run_id: str = "review-1") -> None:
        response = self.client.post(
            "/review/create", data={"scenario_id": scenario, "run_id": run_id}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(f"/review/{run_id}", str(response.url))

    def run_state(self, run_id: str = "review-1") -> Any:
        return self.client.get(f"/runs/{run_id}").json()

    def command(
        self,
        action: str,
        value: str = "",
        run_id: str = "review-1",
        version: str | None = None,
    ) -> Any:
        current = self.run_state(run_id)
        body = {"action": action, "value": value}
        expected = version or (
            current["plan_version"]["id"] if current["plan_version"] else None
        )
        if expected:
            body["expected_plan_version_id"] = expected
        response = self.client.post(f"/review/{run_id}/action", data=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def test_interrupted_acceptance_survives_restart_and_duplicate_creates_one_video(
        self,
    ) -> None:
        self.create()
        self.command("approve")
        self.command("interrupt")
        interrupted = self.run_state()
        self.assertEqual(interrupted["status"], "submitting")
        self.assertIsNone(interrupted["external_job_id"])
        provider = DurableFakeVideoProvider(provider_ledger_path(self.path))
        accepted = provider.job_for_key(interrupted["idempotency_key"])
        self.assertIsNotNone(accepted)
        assert accepted is not None
        self.command("recover")
        self.assertEqual(self.run_state()["status"], "submitting")
        with TestClient(create_app(self.path, clock=lambda: self.now)) as restarted:
            self.assertIn(accepted, restarted.get("/review/review-1").text)
        self.now += 31
        self.command("recover")
        submitted = self.run_state()
        self.assertEqual(submitted["status"], "submitted")
        self.assertEqual(submitted["external_job_id"], accepted)
        self.command("complete", accepted)
        completed = self.run_state()
        self.command("duplicate", accepted)
        self.assertEqual(self.run_state(), completed)
        with Database(self.path).repository() as repository:
            self.assertEqual(len(repository.generated_videos("review-1")), 1)
            self.assertEqual(len(repository.list_provider_events("review-1")), 1)

    def test_duplicate_replays_existing_api_callback(self) -> None:
        self.create()
        self.command("approve")
        self.command("submit")
        job = self.run_state()["external_job_id"]
        response = self.client.post(
            "/callbacks/provider",
            json={"external_job_id": job, "external_event_id": "api-completed"},
        )
        self.assertEqual(response.status_code, 200)
        replay = self.command("duplicate", job)
        self.assertIn("Same completion replayed", replay.text)
        with Database(self.path).repository() as repository:
            self.assertEqual(len(repository.list_provider_events("review-1")), 1)

    def test_targeted_worker_does_not_claim_an_older_ready_run(self) -> None:
        self.create(run_id="older")
        self.command("approve", run_id="older")
        self.create()
        self.command("approve")
        self.command("submit")
        self.assertEqual(self.run_state("older")["status"], "ready")
        self.assertEqual(self.run_state()["status"], "submitted")

    def test_page_puts_the_decision_first_and_fault_controls_after_approval(
        self,
    ) -> None:
        self.create("weak-script")
        page = self.client.get("/review/review-1").text
        self.assertLess(page.index("Finding &amp; evidence"), page.index("Audit trail"))
        self.assertLess(
            page.index("Plan &amp; exact approval"), page.index("Audit trail")
        )
        self.assertNotIn("Worker controls", page)
        self.create(run_id="review-2")
        self.command("approve", run_id="review-2")
        page = self.client.get("/review/review-2").text
        self.assertLess(
            page.index("Plan &amp; exact approval"), page.index("Worker controls")
        )
        self.assertLess(page.index("Worker controls"), page.index("Audit trail"))

    def test_stale_forms_do_not_mutate_the_current_plan(self) -> None:
        self.create("weak-script")
        old = self.run_state()["plan_version"]["id"]
        self.command("bind", "script-api-revised")
        current = self.run_state()
        for action, value in (
            ("tts", "tts-api-revised"),
            ("approve", ""),
            ("edit", "stale edit"),
            ("bind", "script-api-revised"),
        ):
            with self.subTest(action=action):
                response = self.command(action, value, version=old)
                self.assertIn("no longer current", response.text)
                self.assertEqual(self.run_state(), current)

    def test_forms_complete_all_nonvisual_scenarios(self) -> None:
        for scenario, commands in (
            (
                "weak-script",
                (("bind", "script-api-revised"), ("tts", "tts-api-revised")),
            ),
            ("tts-input", (("bind", "tts-api-replacement"),)),
            (
                "environment-mismatch",
                (("choose", "change_avatar"), ("bind", "avatar-api-kitchen")),
            ),
            ("caption-format", ()),
        ):
            with self.subTest(scenario=scenario):
                self.create(scenario, scenario)
                for action, value in commands:
                    self.command(action, value, run_id=scenario)
                self.command("approve", run_id=scenario)
                if scenario == "caption-format":
                    self.command("captions", "Fixed caption.", run_id=scenario)
                else:
                    self.command("submit", run_id=scenario)
                    self.command(
                        "complete",
                        self.run_state(scenario)["external_job_id"],
                        run_id=scenario,
                    )
                self.assertEqual(self.run_state(scenario)["status"], "succeeded")

    def test_caption_success_message_does_not_request_another_approval(self) -> None:
        self.create("caption-format")
        self.command("approve")
        response = self.command("captions", "Fixed caption.")
        self.assertIn("Caption repair accepted", response.text)
        self.assertIn("Accepted video preserved", response.text)
        self.assertNotIn("Review the current plan before approval", response.text)

    def test_shared_capacity_blocks_ready_run_but_permits_unknown_acceptance_recovery(
        self,
    ) -> None:
        for run_id in ("one", "two", "three"):
            self.create(run_id=run_id)
            self.command("approve", run_id=run_id)
        self.command("interrupt", run_id="one")
        self.command("submit", run_id="two")
        blocked = self.command("submit", run_id="three")
        self.assertIn("No claim made", blocked.text)
        self.assertEqual(self.run_state("three")["status"], "ready")
        self.now += 31
        self.command("recover", run_id="one")
        self.assertEqual(self.run_state("one")["status"], "submitted")
        self.command("complete", self.run_state("two")["external_job_id"], run_id="two")
        self.command("submit", run_id="three")
        self.assertEqual(self.run_state("three")["status"], "submitted")

    def test_stale_retry_and_caption_forms_preserve_current_attempt(self) -> None:
        self.create()
        self.command("approve")
        self.command("submit")
        old = self.run_state()["plan_version"]["id"]
        self.command("retry")
        current = self.run_state()
        self.command("retry", version=old)
        self.assertEqual(self.run_state(), current)
        self.create("caption-format", "caption-stale")
        old = self.run_state("caption-stale")["plan_version"]["id"]
        self.command("edit", "Updated local repair rationale", run_id="caption-stale")
        self.command("approve", run_id="caption-stale")
        current = self.run_state("caption-stale")
        self.command("captions", "Stale caption.", run_id="caption-stale", version=old)
        self.assertEqual(self.run_state("caption-stale"), current)

    def test_repository_rechecks_version_after_form_snapshot(self) -> None:
        for scenario, action, value in (
            ("weak-script", "bind", "script-api-revised"),
            ("weak-script", "tts", "tts-api-revised"),
            ("caption-format", "captions", "Fixed caption."),
            ("jerky-video", "retry", ""),
        ):
            with self.subTest(action=action):
                self.check_version_race(scenario, action, value)

    def prepare_race(self, scenario: str, action: str) -> str:
        run_id = "race-" + action
        self.create(scenario, run_id)
        if action == "tts":
            self.command("bind", "script-api-revised", run_id=run_id)
        if action in {"captions", "retry"}:
            self.command("approve", run_id=run_id)
        if action == "retry":
            self.command("submit", run_id=run_id)
        return run_id

    def check_version_race(self, scenario: str, action: str, value: str) -> None:
        run_id = self.prepare_race(scenario, action)
        old = self.run_state(run_id)["plan_version"]["id"]
        original = DemoControls._command
        concurrent: list[WorkflowRun] = []

        def change_before_write(
            controls: DemoControls,
            repository: WorkflowRepository,
            run: WorkflowRun,
            command: UiCommand,
        ) -> None:
            if action == "retry":
                updated = repository.request_retry(
                    run.id,
                    expected_plan_version_id=repository.get(run.id).plan_version_id,
                )
            else:
                assert run.plan is not None
                updated = repository.revise_plan(
                    run.id,
                    plan=replace(run.plan, rationale="Concurrent review change"),
                    expected_plan_version_id=old,
                )
                if action == "captions":
                    updated = repository.approve(
                        run.id, plan_version_id=updated.plan_version_id
                    )
            concurrent.append(updated)
            original(controls, repository, run, command)

        with patch.object(DemoControls, "_command", change_before_write):
            response = self.command(action, value, run_id=run_id, version=old)
        self.assertIn("no longer current", response.text)
        with Database(self.path).repository() as repository:
            self.assertEqual(repository.get(run_id), concurrent[0])

    def test_dynamic_html_is_escaped_and_css_is_served(self) -> None:
        self.create()
        payload = '<script>alert("x")</script>'
        response = self.command("edit", payload)
        self.assertNotIn(payload, response.text)
        self.assertIn("&lt;script&gt;", response.text)
        response = self.client.get("/", params={"message": payload})
        self.assertNotIn(payload, response.text)
        self.assertEqual(self.client.get("/assets/review.css").status_code, 200)
        self.assertEqual(self.client.get("/review/missing").status_code, 404)

    def test_foreign_origin_and_host_forms_cannot_create_runs(self) -> None:
        body = {"scenario_id": "jerky-video", "run_id": "foreign"}
        self.assertEqual(
            self.client.post(
                "/review/create",
                data=body,
                headers={"Origin": "https://foreign.example"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/review/create", data=body, headers={"Host": "foreign.example"}
            ).status_code,
            400,
        )
        self.assertEqual(self.client.get("/runs/foreign").status_code, 404)
        self.assertEqual(
            self.client.post(
                "/review/create", data={**body, "extra": "field"}
            ).status_code,
            422,
        )

    def test_duplicate_before_completion_and_job_from_another_run_are_rejected(
        self,
    ) -> None:
        self.create()
        self.command("approve")
        self.command("submit")
        job = self.run_state()["external_job_id"]
        self.command("duplicate", job)
        self.assertEqual(self.run_state()["status"], "submitted")
        self.create(run_id="other")
        response = self.command("complete", job, run_id="other")
        self.assertIn("different run", response.text)
        self.assertEqual(self.run_state()["status"], "submitted")


if __name__ == "__main__":
    unittest.main()
