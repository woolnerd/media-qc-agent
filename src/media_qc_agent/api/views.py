"""Read-only review resources and synthetic run creation."""

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder

from media_qc_agent.api.database import Database
from media_qc_agent.api.scenarios import REPLACEMENTS, SCENARIOS, create_scenario_run
from media_qc_agent.api.schemas import CreateRun


def scenario_routes(database: Database) -> APIRouter:
    router = APIRouter()

    @router.get("/scenarios")
    def scenarios() -> object:
        return {"scenarios": jsonable_encoder(SCENARIOS), "replacements": REPLACEMENTS}

    @router.post("/scenarios/{scenario_id}/runs", status_code=201)
    def create_run(scenario_id: str, body: CreateRun) -> object:
        if scenario_id not in {scenario.id for scenario in SCENARIOS}:
            raise KeyError(scenario_id)
        with database.repository() as repository:
            return create_scenario_run(repository, scenario_id, body.run_id)

    return router


def read_routes(database: Database) -> APIRouter:
    router = APIRouter()
    router.include_router(scenario_routes(database))

    @router.get("/runs/{run_id}")
    def run(run_id: str) -> object:
        with database.repository() as repository:
            return repository.get(run_id)

    @router.get("/runs/{run_id}/findings")
    def findings(run_id: str) -> object:
        with database.repository() as repository:
            finding = repository.get_quality_finding(run_id)
            return {
                "finding": finding,
                "evidence": repository.get_quality_evidence(finding.id),
            }

    @router.get("/runs/{run_id}/plans")
    def plans(run_id: str) -> object:
        with database.repository() as repository:
            current = repository.get(run_id)
            versions = repository.get_plan_versions(run_id)
            return {
                "current_plan_version_id": current.plan_version_id,
                "versions": versions,
                "approvals": tuple(
                    repository.get_plan_approval(version.id) for version in versions
                ),
            }

    @router.get("/artifacts/{version_id}")
    def artifact(version_id: str) -> object:
        with database.repository() as repository:
            return repository.get_artifact_version(version_id)

    return router
