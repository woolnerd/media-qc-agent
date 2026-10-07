"""Pure routing decisions for each arm of the baseline comparison.

- rules-only: deterministic gates have authority; anything they miss goes to a
  human for diagnosis.
- single-shot: one model turn per case decides, with gate evidence as input.
- layered: gates keep authority; the model interprets only gate-silent cases.

No arm chooses which evidence to inspect: all receive the same precomputed
gate and reviewer evidence. Clarification and replacement reassessment are
deterministic in every arm.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from media_qc_agent.agent.interpretation import repair_scopes
from media_qc_agent.agent.tracing import InterpretationTurn, TurnOutcome
from media_qc_agent.cli.baseline_cases import GateReport
from media_qc_agent.domain.evidence import EvidenceInput
from media_qc_agent.domain.models import (
    ArtifactKind,
    ClarificationRequest,
    FailureKind,
    QualityFinding,
    RepairAction,
)
from media_qc_agent.domain.planner import plan_repair


class Route(StrEnum):
    GATE = "gate"
    MODEL = "model"
    HUMAN = "human"


@dataclass(frozen=True)
class ArmDecision:
    """Who decided, and what reaches human approval or branch choice."""

    route: Route
    kind: FailureKind | None
    scopes: Mapping[RepairAction, frozenset[ArtifactKind]]
    creative: bool
    cited: tuple[EvidenceInput, ...]
    model_consulted: bool
    rejected_output: bool

    @property
    def authorized(self) -> bool:
        return self.kind is not None


def _planned(
    finding: QualityFinding,
) -> tuple[dict[RepairAction, frozenset[ArtifactKind]], bool]:
    decision = plan_repair(finding)
    if isinstance(decision, ClarificationRequest):
        return {option.action: option.invalidates for option in decision.options}, True
    return {decision.action: decision.invalidates}, False


def rules_only(gates: GateReport, turn: InterpretationTurn | None) -> ArmDecision:
    del turn  # Never consults the model; the signature matches the other arms.
    finding = gates.finding
    if finding is None:
        return ArmDecision(Route.HUMAN, None, {}, False, (), False, False)
    scopes, creative = _planned(finding)
    return ArmDecision(
        Route.GATE, finding.kind, scopes, creative, gates.evidence, False, False
    )


def single_shot(gates: GateReport, turn: InterpretationTurn | None) -> ArmDecision:
    del gates  # Gate evidence reaches the model through the request only.
    if turn is None:
        raise ValueError("the single-shot arm needs a model turn")
    interpretation = turn.interpretation
    if interpretation is None or interpretation.finding is None:
        cited = interpretation.evidence if interpretation else ()
        rejected = turn.outcome is TurnOutcome.REJECTED
        return ArmDecision(Route.HUMAN, None, {}, False, cited, True, rejected)
    return ArmDecision(
        Route.MODEL,
        interpretation.finding.kind,
        repair_scopes(interpretation),
        isinstance(interpretation.decision, ClarificationRequest),
        interpretation.evidence,
        True,
        False,
    )


def layered(gates: GateReport, turn: InterpretationTurn | None) -> ArmDecision:
    if gates.finding is not None:
        return rules_only(gates, None)
    return single_shot(gates, turn)


Arm = Callable[[GateReport, InterpretationTurn | None], ArmDecision]
BASELINE_ARM = "rules_only"
ARMS: dict[str, Arm] = {
    BASELINE_ARM: rules_only,
    "single_shot": single_shot,
    "layered": layered,
}
