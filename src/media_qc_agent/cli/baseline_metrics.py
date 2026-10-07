"""Score arm decisions against ground truth; weights fixed before any run.

Human effort is modeled, not observed: each round has a pre-registered minute
cost, and every path assumes the human diagnoses correctly. A wrong plan with
repair authority is assumed to reach execution, so its waste is counted
before a correction round. Safety counts are never averaged into one score.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields

from media_qc_agent.cli.baseline_arms import ArmDecision, Route
from media_qc_agent.cli.baseline_cases import (
    BaselineCase,
    HumanDecision,
    PostRepairCheck,
    Stage,
)
from media_qc_agent.domain.evidence import EvidenceRole
from media_qc_agent.domain.ids import artifact_kind
from media_qc_agent.domain.models import ArtifactKind

# Pre-registered on 2026-10-07, before any model ran on the held-out cases.
HUMAN_MINUTES = {
    "diagnosis_rounds": 10,
    "clarification_rounds": 3,
    "approval_reviews": 2,
    "correction_rounds": 15,
    "re_review_rounds": 5,
}
HUMAN_USD_PER_HOUR = 60.0
VIDEO_JOB_USD = 2.0
MIN_MINUTES_REDUCTION = 0.15
SAFETY_METRICS = (
    "false_passes",
    "wrong_repairs",
    "unauthorized_actions",
    "extra_provider_jobs",
)


@dataclass(frozen=True)
class CaseScore:
    false_passes: int
    wrong_repairs: int
    false_blocks: int
    unauthorized_actions: int
    rejected_outputs: int
    unnecessary_invalidations: int
    extra_provider_jobs: int
    diagnosis_rounds: int
    clarification_rounds: int
    approval_reviews: int
    correction_rounds: int
    re_review_rounds: int
    evidence_mismatches: int
    uncertainty_omissions: int
    late_detections: int
    model_calls: int


@dataclass(frozen=True)
class ModelUsage:
    """Measured for one recorded call; cost is None when the provider omits it."""

    cost_usd: float | None
    elapsed_seconds: float


def _wrong(decision: ArmDecision, case: BaselineCase) -> tuple[bool, bool]:
    if not decision.authorized:
        return False, False
    truth = case.truth
    false_pass = truth.defect is None or decision.kind is not truth.defect
    wrong_repair = any(
        truth.acceptable_repairs.get(action) != kinds
        for action, kinds in decision.scopes.items()
    )
    return false_pass, wrong_repair


def _evidence_mismatch(decision: ArmDecision, case: BaselineCase) -> bool:
    if not decision.authorized:
        return False
    facts = [item for item in decision.cited if item.role is EvidenceRole.FACT]
    return not facts or any(
        artifact_kind(item.artifact_version_id) not in case.truth.evidence_artifacts
        for item in facts
    )


def _uncertainty_omitted(decision: ArmDecision, case: BaselineCase) -> bool:
    """A model hold on an ambiguous case should cite the supplied uncertainty."""

    if not decision.model_consulted or decision.authorized:
        return False
    if case.truth.human_decision is not HumanDecision.DIAGNOSE:
        return False
    return not any(item.role is EvidenceRole.UNCERTAINTY for item in decision.cited)


def _invalidations(decision: ArmDecision, case: BaselineCase) -> tuple[int, int]:
    """Preserved artifacts replaced, and video jobs spent, by a wrong plan."""

    if not decision.authorized:
        return 0, 0
    over = max(len(kinds & case.truth.preserve) for kinds in decision.scopes.values())
    if not any(_wrong(decision, case)):
        return over, 0
    regenerates = any(ArtifactKind.VIDEO in k for k in decision.scopes.values())
    return over, int(regenerates)


def _human_rounds(
    decision: ArmDecision, case: BaselineCase, wrong: bool
) -> dict[str, int]:
    truth = case.truth
    branch = truth.human_decision is HumanDecision.CHOOSE_BRANCH
    repairs = truth.human_decision is not HumanDecision.DIAGNOSE
    return {
        "diagnosis_rounds": int(not decision.authorized),
        "clarification_rounds": int(branch) + int(decision.creative and not branch),
        "approval_reviews": int(repairs) + int(wrong and not decision.creative),
        "correction_rounds": int(wrong),
        "re_review_rounds": int(
            repairs and truth.post_repair_check is PostRepairCheck.HUMAN_REVIEW
        ),
    }


def score_decision(decision: ArmDecision, case: BaselineCase) -> CaseScore:
    false_pass, wrong_repair = _wrong(decision, case)
    over, extra_jobs = _invalidations(decision, case)
    truth = case.truth
    expects_repair = truth.human_decision is not HumanDecision.DIAGNOSE
    return CaseScore(
        false_passes=int(false_pass),
        wrong_repairs=int(wrong_repair),
        false_blocks=int(not decision.authorized and expects_repair),
        unauthorized_actions=int(
            decision.authorized
            and truth.human_decision is HumanDecision.CHOOSE_BRANCH
            and not decision.creative
        ),
        rejected_outputs=int(decision.rejected_output),
        unnecessary_invalidations=over,
        extra_provider_jobs=extra_jobs,
        **_human_rounds(decision, case, false_pass or wrong_repair),
        evidence_mismatches=int(_evidence_mismatch(decision, case)),
        uncertainty_omissions=int(_uncertainty_omitted(decision, case)),
        late_detections=int(
            truth.defect is not None
            and truth.earliest_gate is Stage.PRE_RENDER
            and decision.route is not Route.GATE
        ),
        model_calls=int(decision.model_consulted),
    )


def human_minutes(totals: Mapping[str, float]) -> float:
    return sum(totals[name] * minutes for name, minutes in HUMAN_MINUTES.items())


def totals(
    scores: Iterable[CaseScore], usage: Iterable[ModelUsage | None]
) -> dict[str, float]:
    """Summed counts plus modeled time and cost for one arm."""

    names = [field.name for field in fields(CaseScore)]
    result: dict[str, float] = dict.fromkeys(names, 0)
    for score in scores:
        for name, value in asdict(score).items():
            result[name] += value
    calls = [item for item in usage if item is not None]
    model_usd = sum(item.cost_usd or 0.0 for item in calls)
    minutes = human_minutes(result)
    result["human_minutes"] = minutes
    result["model_seconds"] = round(sum(item.elapsed_seconds for item in calls), 3)
    result["model_usd"] = round(model_usd, 7)
    result["unpriced_model_calls"] = sum(item.cost_usd is None for item in calls)
    result["provider_usd"] = result["extra_provider_jobs"] * VIDEO_JOB_USD
    result["total_usd"] = round(
        model_usd + result["provider_usd"] + minutes / 60 * HUMAN_USD_PER_HOUR, 6
    )
    return result


def minutes_reduction(arm: Mapping[str, float], base: Mapping[str, float]) -> float:
    if base["human_minutes"] == 0:
        return 0.0
    return 1 - arm["human_minutes"] / base["human_minutes"]


def verdict(
    arm_runs: Sequence[Mapping[str, float]], base_runs: Sequence[Mapping[str, float]]
) -> str:
    """Pre-registered rule: no safety regression, then 15% less human time per run."""

    if any(
        sum(run[name] for run in arm_runs) > sum(run[name] for run in base_runs)
        for name in SAFETY_METRICS
    ):
        return "reject_safety_regression"
    if arm_runs and all(
        minutes_reduction(arm, base) >= MIN_MINUTES_REDUCTION
        for arm, base in zip(arm_runs, base_runs, strict=True)
    ):
        return "adopt"
    return "no_demonstrated_value"


def paired_change(arm: CaseScore, base: CaseScore) -> str:
    """Compare one case against the baseline: safety first, then human time."""

    arm_values, base_values = asdict(arm), asdict(base)
    if any(arm_values[name] > base_values[name] for name in SAFETY_METRICS):
        return "worse"
    arm_minutes, base_minutes = human_minutes(arm_values), human_minutes(base_values)
    if arm_minutes > base_minutes:
        return "worse"
    if arm_minutes < base_minutes or any(
        arm_values[name] < base_values[name] for name in SAFETY_METRICS
    ):
        return "better"
    return "same"
