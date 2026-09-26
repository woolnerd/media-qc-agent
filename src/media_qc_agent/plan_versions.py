"""Pure serialization and policy checks for immutable plan snapshots."""

import json
from dataclasses import asdict
from typing import Any

from .domain import (
    ArtifactKind,
    ClarificationRequest,
    QualityFinding,
    RepairAction,
    RepairPlan,
)
from .planner import plan_repair, select_repair
from .workflow import RepairPlanVersion, VideoSources


def validate_plan_revision(finding: QualityFinding, plan: RepairPlan) -> None:
    decision = plan_repair(finding)
    expected = (
        select_repair(decision, plan.action)
        if isinstance(decision, ClarificationRequest)
        else decision
    )
    if (plan.action, plan.invalidates, plan.requires_repair_input) != (
        expected.action,
        expected.invalidates,
        expected.requires_repair_input,
    ):
        raise ValueError("plan revision violates minimum-repair policy")
    if not plan.rationale.strip():
        raise ValueError("plan rationale must not be blank")


def encode_plan_version(version: RepairPlanVersion) -> str:
    return json.dumps(asdict(version), default=lambda value: sorted(value))


def decode_plan_version(snapshot: str, created_at: str) -> RepairPlanVersion:
    data: dict[str, Any] = json.loads(snapshot)
    plan = data["plan"]
    data["plan"] = RepairPlan(
        RepairAction(plan["action"]),
        frozenset(ArtifactKind(k) for k in plan["invalidates"]),
        plan["requires_repair_input"],
        plan["rationale"],
    )
    data["sources"] = VideoSources(**data["sources"])
    data["replacement_choices"] = tuple(
        (ArtifactKind(k), v) for k, v in data["replacement_choices"]
    )
    data["created_at"] = created_at
    return RepairPlanVersion(**data)
