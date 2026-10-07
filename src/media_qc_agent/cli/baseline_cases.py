"""Held-out cases with structured artifacts and ground truth recorded in advance.

Every arm of the baseline comparison runs the same deterministic gates on the
same artifacts. Gate evidence comes first in the interpretation request,
followed by reviewer-attached evidence that no gate produces.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from media_qc_agent.agent.contracts import InterpretationRequest
from media_qc_agent.cli.evaluate import DEFAULT_DATASET
from media_qc_agent.domain.evidence import EvidenceInput, EvidenceRole
from media_qc_agent.domain.models import (
    ArtifactKind,
    FailureKind,
    QualityFinding,
    RepairAction,
)
from media_qc_agent.quality.captions import CaptionCue, validate_captions
from media_qc_agent.quality.environment import (
    Environment,
    ScriptScene,
    check_script_avatar_compatibility,
)
from media_qc_agent.quality.spoken_text import (
    Notation,
    SpokenTextCapabilities,
    prepare_spoken_text,
)
from media_qc_agent.quality.visual_quality import MotionSample, check_jerky_video

BASELINE_DATASET = DEFAULT_DATASET.parent / "baseline-cases-v1.json"
_CATEGORIES = {"clear", "ambiguous", "adversarial"}


class HumanDecision(StrEnum):
    APPROVE_REPAIR = "approve_repair"
    CHOOSE_BRANCH = "choose_branch"
    DIAGNOSE = "diagnose"


class Stage(StrEnum):
    PRE_RENDER = "pre_render"
    POST_RENDER = "post_render"


class PostRepairCheck(StrEnum):
    GATE = "gate"
    HUMAN_REVIEW = "human_review"


@dataclass(frozen=True)
class CaseArtifacts:
    script_text: str
    scene: ScriptScene
    avatar_environment: Environment
    tts_candidate_text: str
    tts_capabilities: SpokenTextCapabilities
    captions: tuple[CaptionCue, ...]
    motion: tuple[MotionSample, ...]


@dataclass(frozen=True)
class GroundTruth:
    """What should happen, annotated by hand before any arm ran."""

    defect: FailureKind | None
    evidence_artifacts: frozenset[ArtifactKind]
    earliest_gate: Stage
    acceptable_repairs: Mapping[RepairAction, frozenset[ArtifactKind]]
    preserve: frozenset[ArtifactKind]
    human_decision: HumanDecision
    post_repair_check: PostRepairCheck
    post_repair_outcome: str


@dataclass(frozen=True)
class BaselineCase:
    id: str
    failure_class: FailureKind
    category: str
    gate_detects: bool
    versions: Mapping[ArtifactKind, str]
    artifacts: CaseArtifacts
    feedback: str
    reviewer_evidence: tuple[EvidenceInput, ...]
    truth: GroundTruth


@dataclass(frozen=True)
class GateReport:
    """Deterministic findings; more than one finding is a conflict for a human."""

    findings: tuple[QualityFinding, ...]
    evidence: tuple[EvidenceInput, ...]

    @property
    def finding(self) -> QualityFinding | None:
        return self.findings[0] if len(self.findings) == 1 else None


def _fields(value: Any, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("baseline dataset object has missing or extra fields")
    return value


def _artifacts(data: dict[str, Any]) -> CaseArtifacts:
    data = _fields(data, {"script", "avatar", "tts_input", "captions", "video"})
    script, tts, video = data["script"], data["tts_input"], data["video"]
    scene = ScriptScene(
        Environment(script["scene"]["environment"]), script["scene"]["evidence_phrase"]
    )
    scene.validate_text(script["text"])
    boundaries = set(video["shot_boundaries"])
    return CaseArtifacts(
        script_text=script["text"],
        scene=scene,
        avatar_environment=Environment(data["avatar"]["environment"]),
        tts_candidate_text=tts["candidate_text"],
        tts_capabilities=SpokenTextCapabilities(
            tts["provider"],
            tts["model"],
            frozenset(Notation(item) for item in tts["supported_notation"]),
        ),
        captions=tuple(
            CaptionCue(cue["start_ms"], cue["end_ms"], cue["text"])
            for cue in data["captions"]
        ),
        motion=tuple(
            MotionSample(index, float(px), index in boundaries)
            for index, px in enumerate(video["motion_px"])
        ),
    )


def _kinds(values: Any) -> frozenset[ArtifactKind]:
    return frozenset(ArtifactKind(value) for value in values)


def _truth(data: dict[str, Any]) -> GroundTruth:
    data = _fields(
        data,
        {
            "defect",
            "evidence_artifacts",
            "earliest_gate",
            "acceptable_repairs",
            "preserve",
            "human_decision",
            "post_repair_check",
            "post_repair_outcome",
        },
    )
    repairs = {
        RepairAction(action): _kinds(kinds)
        for action, kinds in data["acceptable_repairs"].items()
    }
    truth = GroundTruth(
        defect=FailureKind(data["defect"]) if data["defect"] else None,
        evidence_artifacts=_kinds(data["evidence_artifacts"]),
        earliest_gate=Stage(data["earliest_gate"]),
        acceptable_repairs=repairs,
        preserve=_kinds(data["preserve"]),
        human_decision=HumanDecision(data["human_decision"]),
        post_repair_check=PostRepairCheck(data["post_repair_check"]),
        post_repair_outcome=data["post_repair_outcome"],
    )
    _check_truth(truth)
    return truth


def _check_truth(truth: GroundTruth) -> None:
    replaced = frozenset().union(*truth.acceptable_repairs.values())
    if truth.preserve != frozenset(ArtifactKind) - replaced:
        raise ValueError("preserved artifacts must be exactly those no repair replaces")
    diagnose = truth.human_decision is HumanDecision.DIAGNOSE
    if diagnose != (truth.defect is None) or diagnose == bool(truth.acceptable_repairs):
        raise ValueError("only cases without a supported defect need diagnosis")


def _case(data: dict[str, Any]) -> BaselineCase:
    data = _fields(
        data,
        {
            "id",
            "failure_class",
            "category",
            "gate_detects",
            "input_versions",
            "artifacts",
            "feedback",
            "reviewer_evidence",
            "ground_truth",
        },
    )
    if data["category"] not in _CATEGORIES:
        raise ValueError("unknown case category")
    versions = {ArtifactKind(k): v for k, v in data["input_versions"].items()}
    if set(versions) != set(ArtifactKind):
        raise ValueError("every artifact kind needs an input version")
    return BaselineCase(
        id=data["id"],
        failure_class=FailureKind(data["failure_class"]),
        category=data["category"],
        gate_detects=data["gate_detects"] is True,
        versions=versions,
        artifacts=_artifacts(data["artifacts"]),
        feedback=data["feedback"],
        reviewer_evidence=tuple(
            EvidenceInput(
                EvidenceRole(item["role"]),
                versions[ArtifactKind(item["artifact"])],
                item["statement"],
                item["observed"],
                item["limit"],
            )
            for item in data["reviewer_evidence"]
        ),
        truth=_truth(data["ground_truth"]),
    )


def load_baseline_cases(path: Path = BASELINE_DATASET) -> tuple[BaselineCase, ...]:
    data = _fields(
        json.loads(path.read_text()), {"schema_version", "description", "cases"}
    )
    if data["schema_version"] != 1 or not data["cases"]:
        raise ValueError("expected a nonempty version-1 baseline dataset")
    cases = tuple(_case(item) for item in data["cases"])
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("case IDs must be unique")
    return cases


def _environment_gate(case: BaselineCase) -> GateReport:
    script = case.versions[ArtifactKind.SCRIPT]
    avatar = case.versions[ArtifactKind.AVATAR]
    check = check_script_avatar_compatibility(
        script_version_id=script,
        authored_text=case.artifacts.script_text,
        scene=case.artifacts.scene,
        avatar_version_id=avatar,
        avatar_environment=case.artifacts.avatar_environment,
    )
    if check.finding is None or check.evidence is None:
        return GateReport((), ())
    found = check.evidence
    return GateReport(
        (check.finding,),
        (
            EvidenceInput(
                EvidenceRole.FACT,
                script,
                f"Environment check: the script mentions {found.script_phrase!r} "
                f"and declares a {found.required_environment.value} scene.",
            ),
            EvidenceInput(
                EvidenceRole.FACT,
                avatar,
                "Environment check: the avatar's declared environment is "
                f"{found.avatar_environment.value}.",
            ),
        ),
    )


def _spoken_text_gate(case: BaselineCase) -> GateReport:
    tts = case.versions[ArtifactKind.TTS_INPUT]
    capabilities = case.artifacts.tts_capabilities
    result = prepare_spoken_text(
        case.artifacts.script_text,
        capabilities,
        candidate_text=case.artifacts.tts_candidate_text,
    )
    if not result.issues:
        return GateReport((), ())
    finding = QualityFinding(
        FailureKind.TTS_INPUT_COMPATIBILITY,
        f"Spoken-text check found {len(result.issues)} unsafe notation issue(s).",
        1.0,
    )
    limit = f"notation declared for {capabilities.provider}/{capabilities.model}"
    return GateReport(
        (finding,),
        tuple(
            EvidenceInput(
                EvidenceRole.FACT,
                tts,
                f"Spoken-text check {issue.code}: {issue.explanation}",
                issue.token,
                limit,
            )
            for issue in result.issues
        ),
    )


def _caption_gate(case: BaselineCase) -> GateReport:
    captions = case.versions[ArtifactKind.CAPTIONS]
    check = validate_captions(case.artifacts.captions)
    if check.valid:
        return GateReport((), ())
    finding = QualityFinding(
        FailureKind.CAPTION_FORMAT,
        f"Caption check found {len(check.evidence)} rule violation(s).",
        1.0,
    )
    return GateReport(
        (finding,),
        tuple(
            EvidenceInput(
                EvidenceRole.FACT,
                captions,
                f"Caption check {item.rule} at cue {item.cue_index}.",
                item.observed,
                item.limit,
            )
            for item in check.evidence
        ),
    )


def _visual_gate(case: BaselineCase) -> GateReport:
    video = case.versions[ArtifactKind.VIDEO]
    check = check_jerky_video(video, case.artifacts.motion)
    if check.finding is not None:
        return GateReport(
            (check.finding,),
            tuple(
                EvidenceInput(
                    EvidenceRole.FACT,
                    video,
                    f"Motion check: same-shot jump between frames "
                    f"{item.from_frame} and {item.to_frame}.",
                    f"{item.jump_px_per_frame:g} px/frame",
                    f"{item.threshold_px:g} px/frame",
                )
                for item in check.evidence
            ),
        )
    if check.review_needed and check.reason is not None:
        uncertainty = EvidenceInput(
            EvidenceRole.UNCERTAINTY, video, f"Motion check: {check.reason}."
        )
        return GateReport((), (uncertainty,))
    return GateReport((), ())


def run_gates(case: BaselineCase) -> GateReport:
    """Every deterministic gate, in pipeline order, on the case's artifacts."""

    reports = (
        _environment_gate(case),
        _spoken_text_gate(case),
        _caption_gate(case),
        _visual_gate(case),
    )
    return GateReport(
        tuple(finding for report in reports for finding in report.findings),
        tuple(item for report in reports for item in report.evidence),
    )


def interpretation_request(
    case: BaselineCase, gates: GateReport
) -> InterpretationRequest:
    """The single request every model arm sees: gate then reviewer evidence."""

    return InterpretationRequest(
        case.feedback,
        tuple(case.versions[kind] for kind in ArtifactKind),
        gates.evidence + case.reviewer_evidence,
    )
