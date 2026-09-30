"""Commands delegate transition and approval decisions to the repository."""

from fastapi import APIRouter

from media_qc_agent.api.schemas import (
    ApprovePlan,
    BindArtifact,
    ProviderCompletion,
    RepairCaptions,
    RevisePlan,
    SelectRepair,
)
from media_qc_agent.quality.captions import CaptionCue
from media_qc_agent.workflow.database import Database


def action_routes(database: Database) -> APIRouter:
    router = APIRouter()
    router.include_router(input_routes(database))

    @router.post("/runs/{run_id}/plan")
    def revise_plan(run_id: str, body: RevisePlan) -> object:
        with database.repository() as repository:
            return repository.revise_plan(
                run_id,
                plan=body.to_plan(),
                expected_plan_version_id=body.expected_plan_version_id,
            )

    @router.post("/runs/{run_id}/approve")
    def approve(run_id: str, body: ApprovePlan) -> object:
        with database.repository() as repository:
            return repository.approve(run_id, plan_version_id=body.plan_version_id)

    @router.post("/runs/{run_id}/retry")
    def retry(run_id: str) -> object:
        with database.repository() as repository:
            return repository.request_retry(run_id)

    router.include_router(completion_routes(database))
    return router


def input_routes(database: Database) -> APIRouter:
    router = APIRouter()

    @router.post("/runs/{run_id}/select-repair")
    def select_repair(run_id: str, body: SelectRepair) -> object:
        with database.repository() as repository:
            return repository.select_repair(run_id, body.action)

    @router.post("/runs/{run_id}/replacement")
    def bind_replacement(run_id: str, body: BindArtifact) -> object:
        with database.repository() as repository:
            return repository.bind_replacement(run_id, body.version_id)

    @router.post("/runs/{run_id}/tts-input")
    def bind_tts_input(run_id: str, body: BindArtifact) -> object:
        with database.repository() as repository:
            return repository.bind_tts_input(run_id, body.version_id)

    return router


def completion_routes(database: Database) -> APIRouter:
    router = APIRouter()

    @router.post("/runs/{run_id}/captions")
    def captions(run_id: str, body: RepairCaptions) -> object:
        with database.repository() as repository:
            return repository.record_caption_repair(
                run_id=run_id,
                version_id=body.version_id,
                cues=tuple(
                    CaptionCue(cue.start_ms, cue.end_ms, cue.text) for cue in body.cues
                ),
            )

    @router.post("/callbacks/provider")
    def provider_completion(body: ProviderCompletion) -> object:
        with database.repository() as repository:
            run = repository.record_completion(
                external_job_id=body.external_job_id,
                external_event_id=body.external_event_id,
            )
            return {
                "run": run,
                "event": repository.get_provider_event(body.external_event_id),
            }

    return router
