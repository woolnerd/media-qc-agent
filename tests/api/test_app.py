import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

from fastapi.testclient import TestClient

from media_qc_agent.api.app import create_app
from media_qc_agent.api.database import Database
from media_qc_agent.workflow.executor import WorkflowExecutor
from media_qc_agent.workflow.provider import FakeVideoProvider


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "api.sqlite3"
        self.client = TestClient(create_app(self.path))
        self.client.__enter__()
        self.provider = FakeVideoProvider()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def create(self, scenario: str = "jerky-video", run_id: str = "run-1") -> Any:
        response = self.client.post(
            f"/scenarios/{scenario}/runs", json={"run_id": run_id}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def approve(self, run_id: str = "run-1") -> Any:
        run = self.client.get(f"/runs/{run_id}").json()
        response = self.client.post(
            f"/runs/{run_id}/approve",
            json={"plan_version_id": run["plan_version"]["id"]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def submit(self, run_id: str = "run-1") -> str:
        # Worker behavior is exercised through the existing executor, not an HTTP submit.
        with Database(self.path).repository() as repository:
            run = WorkflowExecutor(
                repository=repository, provider=self.provider
            ).submit(run_id)
        assert run.external_job_id is not None
        return run.external_job_id

    def callback(self, job_id: str, event_id: str) -> Any:
        return self.client.post(
            "/callbacks/provider",
            json={"external_job_id": job_id, "external_event_id": event_id},
        )

    def test_all_five_scenarios_have_findings_evidence_and_plan_resources(self) -> None:
        response = self.client.get("/scenarios")
        self.assertEqual(response.status_code, 200)
        scenarios = response.json()["scenarios"]
        self.assertEqual(len(scenarios), 5)
        for index, scenario in enumerate(scenarios):
            with self.subTest(scenario=scenario["id"]):
                self.assertIn("illustrative", scenario["demo_notice"])
                run_id = f"run-{index}"
                self.create(scenario["id"], run_id)
                findings = self.client.get(f"/runs/{run_id}/findings")
                self.assertEqual(findings.status_code, 200)
                self.assertTrue(findings.json()["evidence"])
                self.assertEqual(
                    self.client.get(f"/runs/{run_id}/plans").status_code, 200
                )

    def test_request_validation_rejects_unknown_fields_types_and_blank_ids(
        self,
    ) -> None:
        for body in (
            {},
            {"run_id": 1},
            {"run_id": "RUN-1"},
            {"run_id": "bad--id"},
            {"run_id": " "},
            {"run_id": "run-1", "approved": True},
        ):
            with self.subTest(body=body):
                response = self.client.post("/scenarios/jerky-video/runs", json=body)
                self.assertEqual(response.status_code, 422)
        self.create()
        for body in ({}, {"plan_version_id": " "}, {"plan_version_id": 123}):
            with self.subTest(body=body):
                self.assertEqual(
                    self.client.post("/runs/run-1/approve", json=body).status_code, 422
                )
        self.assertEqual(
            self.client.get("/runs/run-1").json()["status"], "awaiting_approval"
        )

    def test_missing_resources_and_duplicate_ids_have_safe_errors(self) -> None:
        for endpoint in (
            "/runs/missing",
            "/runs/missing/findings",
            "/runs/missing/plans",
            "/artifacts/missing",
        ):
            self.assertEqual(self.client.get(endpoint).status_code, 404)
        self.assertEqual(
            self.client.post(
                "/scenarios/missing/runs", json={"run_id": "run-1"}
            ).status_code,
            404,
        )
        self.create()
        duplicate = self.client.post(
            "/scenarios/jerky-video/runs", json={"run_id": "run-1"}
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertNotIn("UNIQUE", duplicate.text)
        self.assertEqual(self.callback("unknown-job", "event-1").status_code, 404)

    def test_approval_requires_current_version_and_creates_no_provider_job(
        self,
    ) -> None:
        run = self.create()
        self.assertEqual(
            self.client.post(
                "/runs/run-1/approve", json={"plan_version_id": "stale-version"}
            ).status_code,
            409,
        )
        approved = self.approve()
        self.assertEqual(approved["status"], "ready")
        self.assertIsNone(approved["external_job_id"])
        self.assertEqual(self.provider.jobs_created, 0)
        self.assertEqual(
            approved["approval"]["plan_version_id"], run["plan_version"]["id"]
        )

    def test_plan_edit_invalidates_approval_rejects_stale_edits_and_scope_expansion(
        self,
    ) -> None:
        original = self.create()
        self.approve()
        edit = dict(
            original["plan"], expected_plan_version_id=original["plan_version"]["id"]
        )
        edit["rationale"] = "Human-reviewed motion finding."
        edited = self.client.post("/runs/run-1/plan", json=edit)
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertIsNone(edited.json()["approval"])
        self.assertEqual(edited.json()["status"], "awaiting_approval")
        self.assertEqual(
            self.client.post("/runs/run-1/plan", json=edit).status_code, 409
        )
        self.assertEqual(
            self.client.post(
                "/runs/run-1/approve",
                json={"plan_version_id": original["plan_version"]["id"]},
            ).status_code,
            409,
        )
        edit["expected_plan_version_id"] = edited.json()["plan_version"]["id"]
        edit["invalidates"] = [*edit["invalidates"], "script"]
        self.assertEqual(
            self.client.post("/runs/run-1/plan", json=edit).status_code, 409
        )
        history = self.client.get("/runs/run-1/plans").json()
        self.assertEqual(len(history["versions"]), 2)
        self.assertEqual(history["versions"][0]["plan"], original["plan"])

    def test_creative_choice_requires_valid_replacement_before_approval(self) -> None:
        self.create("environment-mismatch")
        self.assertEqual(
            self.client.post(
                "/runs/run-1/approve", json={"plan_version_id": "anything"}
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                "/runs/run-1/select-repair", json={"action": "regenerate_video"}
            ).status_code,
            409,
        )
        selected = self.client.post(
            "/runs/run-1/select-repair", json={"action": "change_avatar"}
        )
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json()["status"], "needs_repair_input")
        self.assertEqual(
            self.client.post(
                "/runs/run-1/replacement", json={"version_id": "script-api-revised"}
            ).status_code,
            409,
        )
        replaced = self.client.post(
            "/runs/run-1/replacement", json={"version_id": "avatar-api-kitchen"}
        )
        self.assertEqual(replaced.status_code, 200, replaced.text)
        self.assertEqual(self.approve()["status"], "ready")

    def test_script_repair_requires_matching_derived_tts(self) -> None:
        self.create("weak-script")
        replaced = self.client.post(
            "/runs/run-1/replacement", json={"version_id": "script-api-revised"}
        )
        self.assertEqual(replaced.status_code, 200)
        self.assertEqual(
            self.client.post(
                "/runs/run-1/approve",
                json={"plan_version_id": replaced.json()["plan_version"]["id"]},
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                "/runs/run-1/tts-input", json={"version_id": "tts-api-replacement"}
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                "/runs/run-1/tts-input", json={"version_id": "tts-api-revised"}
            ).status_code,
            200,
        )
        self.assertEqual(self.approve()["status"], "ready")

    def test_tts_repair_binds_replacement_without_changing_script(self) -> None:
        original = self.create("tts-input")
        response = self.client.post(
            "/runs/run-1/replacement", json={"version_id": "tts-api-replacement"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["sources"]["script_version_id"],
            original["sources"]["script_version_id"],
        )
        self.assertEqual(self.approve()["status"], "ready")

    def test_caption_repair_validates_timing_and_requires_approval(self) -> None:
        original = self.create("caption-format")
        cue: dict[str, object] = {
            "start_ms": 0,
            "end_ms": 1000,
            "text": "Fixed caption.",
        }
        repair = {"version_id": "caption-repaired", "cues": [cue]}
        self.assertEqual(
            self.client.post("/runs/run-1/captions", json=repair).status_code, 409
        )
        self.approve()
        cue["end_ms"] = 0
        self.assertEqual(
            self.client.post("/runs/run-1/captions", json=repair).status_code, 422
        )
        cue["end_ms"] = 1000
        response = self.client.post("/runs/run-1/captions", json=repair)
        self.assertEqual(response.status_code, 200, response.text)
        current = self.client.get("/runs/run-1").json()
        self.assertEqual(current["status"], "succeeded")
        self.assertEqual(
            current["active_video_version_id"], original["active_video_version_id"]
        )
        self.assertEqual(current["active_caption_version_id"], "caption-repaired")
        self.assertIsNone(current["external_job_id"])

    def test_duplicate_and_stale_callbacks_preserve_current_video(self) -> None:
        self.create()
        self.approve()
        old_job = self.submit()
        self.assertEqual(self.client.post("/runs/run-1/retry").status_code, 200)
        self.approve()
        new_job = self.submit()
        stale = self.callback(old_job, "old-completed")
        self.assertEqual(stale.status_code, 200)
        self.assertEqual(stale.json()["event"]["disposition"], "stale")
        completed = self.callback(new_job, "new-completed")
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["event"]["disposition"], "applied")
        video_id = completed.json()["run"]["active_video_version_id"]
        duplicate = self.callback(new_job, "new-completed")
        self.assertEqual(duplicate.json()["run"]["active_video_version_id"], video_id)
        self.assertEqual(self.callback(old_job, "new-completed").status_code, 409)
        artifact = self.client.get(f"/artifacts/{video_id}").json()
        self.assertEqual(len(artifact["source_versions"]), 4)

    def test_submitted_work_cannot_be_edited_or_reapproved(self) -> None:
        original = self.create()
        self.approve()
        self.submit()
        edit = dict(
            original["plan"], expected_plan_version_id=original["plan_version"]["id"]
        )
        self.assertEqual(
            self.client.post("/runs/run-1/plan", json=edit).status_code, 409
        )
        self.assertEqual(
            self.client.post(
                "/runs/run-1/approve",
                json={"plan_version_id": original["plan_version"]["id"]},
            ).status_code,
            409,
        )

    def test_restart_preserves_run_approval_and_does_not_duplicate_fixtures(
        self,
    ) -> None:
        self.create()
        original = self.approve()
        with TestClient(create_app(self.path)) as restarted:
            current = restarted.get("/runs/run-1").json()
            self.assertEqual(current, original)
            self.assertEqual(len(restarted.get("/scenarios").json()["scenarios"]), 5)

    def test_every_repository_connection_enforces_foreign_keys(self) -> None:
        with Database(self.path).repository() as repository:
            # The boundary enables FK checks even though initialize only runs at startup.
            self.assertEqual(
                repository._connection.execute("PRAGMA foreign_keys").fetchone()[0], 1
            )

    def test_concurrent_run_creation_commits_one_run_and_reports_one_conflict(
        self,
    ) -> None:
        barrier = Barrier(2)

        def create() -> int:
            barrier.wait(timeout=5)
            return self.client.post(
                "/scenarios/jerky-video/runs", json={"run_id": "same-run"}
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(create) for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]
        self.assertEqual(sorted(results), [201, 409])
        self.assertEqual(
            len(self.client.get("/runs/same-run/plans").json()["versions"]), 1
        )

    def test_concurrent_callbacks_create_one_replacement_video(self) -> None:
        self.create()
        self.approve()
        job_id = self.submit()
        barrier = Barrier(2)

        def complete() -> Any:
            barrier.wait(timeout=5)
            response = self.callback(job_id, "same-completion")
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()["run"]["active_video_version_id"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(complete) for _ in range(2)]
            versions = [future.result(timeout=15) for future in futures]
        self.assertEqual(versions, [f"video:{job_id}", f"video:{job_id}"])

    def test_factory_does_not_write_until_lifespan_starts(self) -> None:
        path = Path(self.directory.name) / "unstarted" / "api.sqlite3"
        create_app(path)
        self.assertFalse(path.parent.exists())
        with self.assertRaises(ValueError):
            create_app(":memory:")


if __name__ == "__main__":
    unittest.main()
