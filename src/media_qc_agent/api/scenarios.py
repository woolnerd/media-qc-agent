"""Fixed, explicitly illustrative scenarios; no live inference or provider calls."""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import TypeVar

from media_qc_agent.domain.evidence import EvidenceInput, EvidenceRole
from media_qc_agent.domain.models import ArtifactKind, FailureKind, QualityFinding
from media_qc_agent.quality.captions import CaptionCue
from media_qc_agent.quality.environment import Environment, ScriptScene
from media_qc_agent.quality.spoken_text import SpokenTextCapabilities
from media_qc_agent.quality.visual_quality import MotionSample
from media_qc_agent.workflow.models import VideoSources, WorkflowRun
from media_qc_agent.workflow.repository import WorkflowRepository

DEMO_NOTICE = (
    "Demo-grade synthetic fixtures for illustrative purposes. No raw media is "
    "inspected and no real TTS or video quality is certified."
)
SOURCES = VideoSources(
    "script-api-original", "tts-api-original", "avatar-api-neutral", "voice-api-demo"
)
KITCHEN_SOURCES = VideoSources(
    "script-api-kitchen", "tts-api-kitchen", "avatar-api-office", "voice-api-demo"
)
T = TypeVar("T")


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    demo_notice: str = DEMO_NOTICE


SCENARIOS = (
    Scenario("weak-script", "Script needs a clearer explanation"),
    Scenario("environment-mismatch", "Kitchen script with an office avatar"),
    Scenario("tts-input", "Review requests replacement spoken text"),
    Scenario("jerky-video", "Synthetic same-shot motion jumps"),
    Scenario("caption-format", "Caption line exceeds the demo limit"),
)
REPLACEMENTS = (
    "script-api-revised",
    "tts-api-revised",
    "tts-api-replacement",
    "avatar-api-kitchen",
)


def _ensure(get: Callable[[], T], create: Callable[[], object]) -> None:
    try:
        get()
    except KeyError:
        create()


def seed_scenarios(repository: WorkflowRepository) -> None:
    """Seed the dedicated demo database once; immutable fixtures survive restarts."""
    for suffix, text, scene in (
        ("original", "A synthetic sentence.", ScriptScene(Environment.NEUTRAL)),
        ("revised", "A clearer synthetic sentence.", ScriptScene(Environment.NEUTRAL)),
        (
            "kitchen",
            "Heat the oven to 450°F.",
            ScriptScene(Environment.KITCHEN, evidence_phrase="oven"),
        ),
    ):
        _ensure(
            partial(repository.get_script_text, f"script-api-{suffix}"),
            partial(
                repository.create_script_version,
                version_id=f"script-api-{suffix}",
                authored_text=text,
                scene=scene,
            ),
        )
    _seed_tts(repository)
    for environment in Environment:
        version_id = f"avatar-api-{environment.value}"
        _ensure(
            partial(repository.get_avatar_environment, version_id),
            partial(
                repository.create_avatar_version,
                version_id=version_id,
                environment=environment,
            ),
        )
    _ensure(
        lambda: repository.get_artifact_version("voice-api-demo"),
        lambda: repository.create_source_version(
            version_id="voice-api-demo", kind=ArtifactKind.VOICE
        ),
    )
    _seed_video_and_captions(repository)


def _seed_tts(repository: WorkflowRepository) -> None:
    for suffix, script_suffix in (
        ("original", "original"),
        ("revised", "revised"),
        ("replacement", "original"),
        ("kitchen", "kitchen"),
    ):
        _ensure(
            partial(repository.get_tts_input_version, f"tts-api-{suffix}"),
            partial(
                repository.create_tts_input_version,
                version_id=f"tts-api-{suffix}",
                script_version_id=f"script-api-{script_suffix}",
                capabilities=SpokenTextCapabilities("fake", "literal", frozenset()),
            ),
        )


def _seed_video_and_captions(repository: WorkflowRepository) -> None:
    _ensure(
        lambda: repository.get_artifact_version("video:api-observed"),
        lambda: repository.create_synthetic_video_version(
            fixture_job_id="api-observed", sources=SOURCES
        ),
    )
    _ensure(
        lambda: repository.get_artifact_version("caption-api-observed"),
        lambda: repository.record_caption_version(
            version_id="caption-api-observed",
            video_version_id="video:api-observed",
            cues=(CaptionCue(0, 1000, "x" * 50),),
        ),
    )


def create_scenario_run(
    repository: WorkflowRepository, scenario_id: str, run_id: str
) -> WorkflowRun:
    if scenario_id == "environment-mismatch":
        result = repository.create_environment_run(
            run_id=run_id, sources=KITCHEN_SOURCES
        )
    elif scenario_id == "jerky-video":
        result = repository.create_visual_quality_run(
            run_id=run_id,
            video_version_id="video:api-observed",
            samples=tuple(
                MotionSample(index, displacement)
                for index, displacement in enumerate((0, 1, 30, 1, 31))
            ),
        )
    elif scenario_id == "caption-format":
        result = repository.create_caption_quality_run(
            run_id=run_id, caption_version_id="caption-api-observed"
        )
    else:
        return _create_review_fixture(repository, scenario_id, run_id)
    assert result is not None, "synthetic scenario must produce its expected finding"
    return result


def _create_review_fixture(
    repository: WorkflowRepository, scenario_id: str, run_id: str
) -> WorkflowRun:
    fixtures = {
        "weak-script": (FailureKind.SCRIPT_QUALITY, "script-api-original"),
        "tts-input": (FailureKind.TTS_INPUT_COMPATIBILITY, "tts-api-original"),
    }
    kind, observed = fixtures[scenario_id]
    return repository.create(
        run_id=run_id,
        finding=QualityFinding(kind, "Synthetic reviewer requests a replacement.", 0.9),
        sources=SOURCES,
        observed_artifact_version_id=observed,
        evidence=(
            EvidenceInput(
                EvidenceRole.FACT, observed, "A fixed demo reviewer requests repair."
            ),
            EvidenceInput(
                EvidenceRole.INFERENCE,
                observed,
                "The fixture selects this failure class.",
            ),
            EvidenceInput(EvidenceRole.UNCERTAINTY, observed, DEMO_NOTICE),
        ),
    )
