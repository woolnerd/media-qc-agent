"""FastAPI factory for a single-process, local-only synthetic review demo."""

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from media_qc_agent.api.actions import action_routes
from media_qc_agent.api.scenarios import DEMO_NOTICE, seed_scenarios
from media_qc_agent.api.views import read_routes
from media_qc_agent.workflow.database import Database


def create_app(database_path: Path | str = ".local/review.sqlite3") -> FastAPI:
    database = Database(Path(database_path))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database.path.parent.mkdir(parents=True, exist_ok=True)
        with database.repository() as repository:
            repository.initialize()
            seed_scenarios(repository)
        yield

    app = FastAPI(
        title="Media QC review demo", description=DEMO_NOTICE, lifespan=lifespan
    )
    app.include_router(read_routes(database))
    app.include_router(action_routes(database))
    _register_errors(app)
    return app


def _register_errors(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        # Raw input may contain nonfinite numbers or sensitive data; omit it.
        detail = [
            {"loc": item["loc"], "type": item["type"], "msg": item["msg"]}
            for item in error.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": detail})

    @app.exception_handler(KeyError)
    async def missing(request: Request, error: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Resource not found"})

    @app.exception_handler(ValueError)
    async def conflict(request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(sqlite3.IntegrityError)
    async def duplicate(
        request: Request, error: sqlite3.IntegrityError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"detail": "Identifier or relationship conflict"}
        )
