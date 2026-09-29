"""Validate HTTP input before handing it to the workflow boundary."""

from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
)

from media_qc_agent.domain.models import ArtifactKind, RepairAction, RepairPlan

Identifier = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=200, pattern=r"\S")
]
RunIdentifier = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=1, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    ),
]
Rationale = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=4000, pattern=r"\S")
]


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateRun(RequestBody):
    run_id: RunIdentifier


class SelectRepair(RequestBody):
    action: RepairAction


class BindArtifact(RequestBody):
    version_id: Identifier


class ApprovePlan(RequestBody):
    plan_version_id: Identifier


class RevisePlan(RequestBody):
    expected_plan_version_id: Identifier
    action: RepairAction
    invalidates: frozenset[ArtifactKind]
    requires_repair_input: StrictBool
    rationale: Rationale

    def to_plan(self) -> RepairPlan:
        return RepairPlan(
            self.action, self.invalidates, self.requires_repair_input, self.rationale
        )


class Cue(RequestBody):
    start_ms: Annotated[StrictInt, Field(ge=0)]
    end_ms: Annotated[StrictInt, Field(gt=0)]
    text: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=1000)]


class RepairCaptions(RequestBody):
    version_id: Identifier
    cues: Annotated[list[Cue], Field(min_length=1, max_length=1000)]


class ProviderCompletion(RequestBody):
    external_job_id: Identifier
    external_event_id: Identifier
