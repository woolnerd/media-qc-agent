"""Native forms adapt to guarded commands; pages contain no JavaScript."""

import sqlite3
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from media_qc_agent.api.demo_controls import DemoControls, UiCommand
from media_qc_agent.api.html_parts import document, panel, run_url
from media_qc_agent.api.review_html import dashboard, review_page
from media_qc_agent.api.scenarios import SCENARIOS, create_scenario_run
from media_qc_agent.api.schemas import CreateRun, Identifier
from media_qc_agent.workflow.database import Database
from media_qc_agent.workflow.repository import WorkflowRepository


class UiCreate(CreateRun):
    scenario_id: Identifier


def _same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is not None and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(
            status_code=403, detail="Review forms require the same origin"
        )


def _redirect(path: str, message: str, *, error: bool = False) -> RedirectResponse:
    return RedirectResponse(
        path + "?" + urlencode({"message": message, "error": str(error).lower()}),
        status_code=303,
    )


def review_routes(database: Database, controls: DemoControls) -> APIRouter:
    router = APIRouter()

    @router.get("/assets/review.css")
    def stylesheet() -> FileResponse:
        return FileResponse(
            Path(__file__).with_name("review.css"), media_type="text/css"
        )

    @router.get("/", response_class=HTMLResponse)
    def index(message: str = "", error: bool = False) -> str:
        with database.connection() as connection, connection:
            repository = WorkflowRepository(connection)
            connection.execute("BEGIN")
            runs = tuple(repository.get(run_id) for run_id in repository.list_run_ids())
        return dashboard(runs, message, error)

    @router.get("/review/{run_id}", response_class=HTMLResponse)
    def review(run_id: str, message: str = "", error: bool = False) -> HTMLResponse:
        try:
            snapshot = controls.snapshot(run_id)
        except KeyError:
            return HTMLResponse(
                document(
                    "Run not found",
                    panel(
                        "Run not found",
                        '<p><a href="/">Return to scenarios and runs</a></p>',
                    ),
                ),
                status_code=404,
            )
        return HTMLResponse(review_page(snapshot, message, error))

    router.include_router(command_routes(database, controls))
    return router


def command_routes(database: Database, controls: DemoControls) -> APIRouter:
    router = APIRouter()
    router.include_router(create_routes(database))

    @router.post("/review/{run_id}/action")
    def action(
        request: Request, run_id: str, body: Annotated[UiCommand, Form()]
    ) -> RedirectResponse:
        _same_origin(request)
        try:
            message = controls.execute(run_id, body)
        except ValueError as error:
            return _redirect(run_url(run_id), str(error), error=True)
        except (KeyError, sqlite3.IntegrityError):
            return _redirect(
                run_url(run_id),
                "Resource missing or identifier conflict. Refresh the run.",
                error=True,
            )
        return _redirect(run_url(run_id), message)

    return router


def create_routes(database: Database) -> APIRouter:
    router = APIRouter()

    @router.post("/review/create")
    def create(request: Request, body: Annotated[UiCreate, Form()]) -> RedirectResponse:
        _same_origin(request)
        if body.scenario_id not in {item.id for item in SCENARIOS}:
            return _redirect("/", "Scenario not found", error=True)
        try:
            with database.repository() as repository:
                create_scenario_run(repository, body.scenario_id, body.run_id)
        except sqlite3.IntegrityError:
            return _redirect(
                "/",
                "That run ID already exists. Open it below or choose a new ID.",
                error=True,
            )
        return _redirect(
            run_url(body.run_id),
            "Scenario created. Inspect its evidence and minimum repair.",
        )

    return router
