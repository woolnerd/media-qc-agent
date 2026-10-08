"""Fixed, explicitly illustrative scenarios; no live inference or provider calls.

Every person, product, script, reviewer note, and provider profile below is
invented for this demo. "Halden Home" is a fictional appliance brand.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import TypeVar

from media_qc_agent.domain.evidence import EvidenceInput, EvidenceRole
from media_qc_agent.domain.models import ArtifactKind, FailureKind, QualityFinding
from media_qc_agent.quality.captions import CaptionCue
from media_qc_agent.quality.environment import Environment, ScriptScene
from media_qc_agent.quality.spoken_text import (
    DEMO_NOTICE as SPOKEN_TEXT_NOTICE,
)
from media_qc_agent.quality.spoken_text import (
    Notation,
    SpokenTextCapabilities,
    prepare_spoken_text,
)
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
APPROVED_SOURCES = VideoSources(
    "script-api-revised", "tts-api-revised", "avatar-api-neutral", "voice-api-demo"
)
KITCHEN_SOURCES = VideoSources(
    "script-api-kitchen", "tts-api-kitchen", "avatar-api-office", "voice-api-demo"
)
OVEN_SOURCES = VideoSources(
    "script-api-oven", "tts-api-oven", "avatar-api-kitchen", "voice-api-demo"
)
T = TypeVar("T")


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    demo_notice: str = DEMO_NOTICE


SCENARIOS = (
    Scenario("weak-script", "Thermostat setup script gives vague steps"),
    Scenario("environment-mismatch", "Oven script assigned to an office avatar"),
    Scenario("tts-input", "Voice reads 450°F as “four fifty F”"),
    Scenario("jerky-video", "Presenter jumps twice in the same shot"),
    Scenario("caption-format", "Caption line runs past 42 characters"),
)
REPLACEMENTS = (
    "script-api-revised",
    "tts-api-revised",
    "tts-api-replacement",
    "avatar-api-kitchen",
)

THERMOSTAT_DRAFT = (
    "Setting up your Halden thermostat is easy. Just press the thing, then the "
    "other thing, and it works. If it does not, try again."
)
THERMOSTAT_STEP = (
    "Press and hold the Halden dial for three seconds until the ring glows blue."
)
THERMOSTAT_REVISED = (
    f"{THERMOSTAT_STEP} Then open the Halden app, choose your home network, and "
    "enter the code shown on the dial."
)
OVEN_PROMPT = "Preheat the oven to 450°F, then slide the tray onto the middle rack."
OVEN_SHORTHAND = (
    "Preheat the oven to 450*F, then roast the vegetables for twenty minutes."
)
OVEN_CANDIDATE = OVEN_SHORTHAND.replace("450*F", "450°F")
VAGUE_STEP = "press the thing, then the other thing"
TTS_PROVIDER = "synthetic-tts"
TTS_MODEL = "studio-v2"
# The original profile wrongly declares that studio-v2 reads temperatures.
CLAIMED_PROFILE = SpokenTextCapabilities(
    TTS_PROVIDER, TTS_MODEL, frozenset({Notation.TEMPERATURE})
)
LITERAL_PROFILE = SpokenTextCapabilities(TTS_PROVIDER, TTS_MODEL, frozenset())
# A slow push-in at 2-4 px/frame with two same-shot jumps near frames 4 and 8.
MOTION_PX = (3, 3, 4, 3, 29, 4, 3, 3, 31, 4, 3, 3)


class FixtureDrift(RuntimeError):
    def __init__(self, version_id: str) -> None:
        super().__init__(
            f"Demo fixture {version_id} differs from the current seed; delete the "
            "demo database (default .local/review.sqlite3) and its provider "
            "ledger, then restart."
        )


def _ensure(
    version_id: str,
    get: Callable[[], T],
    create: Callable[[], object],
    expected: T,
) -> None:
    """Create a fixture once and refuse to reuse different stored content."""

    try:
        stored = get()
    except KeyError:
        create()
        return
    if stored != expected:
        raise FixtureDrift(version_id)


def seed_scenarios(repository: WorkflowRepository) -> None:
    """Seed the dedicated demo database once; immutable fixtures survive restarts."""
    for version_id, text, scene in (
        ("script-api-original", THERMOSTAT_DRAFT, ScriptScene(Environment.NEUTRAL)),
        ("script-api-revised", THERMOSTAT_REVISED, ScriptScene(Environment.NEUTRAL)),
        ("script-api-kitchen", OVEN_PROMPT, ScriptScene(Environment.KITCHEN, "oven")),
        ("script-api-oven", OVEN_SHORTHAND, ScriptScene(Environment.KITCHEN, "oven")),
    ):
        _ensure(
            version_id,
            partial(repository.get_script_text, version_id),
            partial(
                repository.create_script_version,
                version_id=version_id,
                authored_text=text,
                scene=scene,
            ),
            text,
        )
    _seed_tts(repository)
    for environment in Environment:
        version_id = f"avatar-api-{environment.value}"
        _ensure(
            version_id,
            partial(repository.get_avatar_environment, version_id),
            partial(
                repository.create_avatar_version,
                version_id=version_id,
                environment=environment,
            ),
            environment,
        )
    _ensure(
        "voice-api-demo",
        lambda: repository.get_artifact_version("voice-api-demo").kind,
        lambda: repository.create_source_version(
            version_id="voice-api-demo", kind=ArtifactKind.VOICE
        ),
        ArtifactKind.VOICE,
    )
    _seed_video_and_captions(repository)


def _seed_tts(repository: WorkflowRepository) -> None:
    for version_id, script_id, profile, candidate in (
        ("tts-api-original", "script-api-original", LITERAL_PROFILE, None),
        ("tts-api-revised", "script-api-revised", LITERAL_PROFILE, None),
        ("tts-api-kitchen", "script-api-kitchen", LITERAL_PROFILE, None),
        ("tts-api-oven", "script-api-oven", CLAIMED_PROFILE, OVEN_CANDIDATE),
        ("tts-api-replacement", "script-api-oven", LITERAL_PROFILE, OVEN_CANDIDATE),
    ):
        _ensure(
            version_id,
            partial(_tts_identity, repository, version_id),
            partial(
                repository.create_tts_input_version,
                version_id=version_id,
                script_version_id=script_id,
                capabilities=profile,
                candidate_text=candidate,
            ),
            (script_id, profile, candidate or repository.get_script_text(script_id)),
        )


def _tts_identity(
    repository: WorkflowRepository, version_id: str
) -> tuple[str, SpokenTextCapabilities, str]:
    stored = repository.get_tts_input_version(version_id)
    return stored.script_version_id, stored.capabilities, stored.candidate_text


def _seed_video_and_captions(repository: WorkflowRepository) -> None:
    _ensure(
        "video:api-observed",
        lambda: dict(
            repository.get_artifact_version("video:api-observed").source_versions
        ),
        lambda: repository.create_synthetic_video_version(
            fixture_job_id="api-observed", sources=APPROVED_SOURCES
        ),
        dict(APPROVED_SOURCES.dependencies()),
    )
    cues: tuple[CaptionCue, ...] = (CaptionCue(0, 4200, THERMOSTAT_STEP),)
    _ensure(
        "caption-api-observed",
        lambda: repository.get_caption_cues("caption-api-observed"),
        lambda: repository.record_caption_version(
            version_id="caption-api-observed",
            video_version_id="video:api-observed",
            cues=cues,
        ),
        cues,
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
                for index, displacement in enumerate(MOTION_PX)
            ),
        )
    elif scenario_id == "caption-format":
        result = repository.create_caption_quality_run(
            run_id=run_id, caption_version_id="caption-api-observed"
        )
    elif scenario_id == "tts-input":
        result = _create_tts_run(repository, run_id)
    else:
        result = _create_weak_script_run(repository, run_id)
    assert result is not None, "synthetic scenario must produce its expected finding"
    return result


def _create_weak_script_run(repository: WorkflowRepository, run_id: str) -> WorkflowRun:
    script_id = SOURCES.script_version_id
    return repository.create(
        run_id=run_id,
        finding=QualityFinding(
            FailureKind.SCRIPT_QUALITY,
            "Reviewer Priya Natarajan (synthetic): step two says “press the thing, "
            "then the other thing”, so viewers cannot tell which control to use.",
            0.9,
        ),
        sources=SOURCES,
        observed_artifact_version_id=script_id,
        evidence=(
            EvidenceInput(
                EvidenceRole.FACT,
                script_id,
                "The authored script names no control for the setup step.",
                observed=VAGUE_STEP,
            ),
            EvidenceInput(
                EvidenceRole.INFERENCE,
                script_id,
                "Unclear instructions are a script defect; rendering video now "
                "would carry them into every downstream artifact.",
            ),
            EvidenceInput(EvidenceRole.UNCERTAINTY, script_id, DEMO_NOTICE),
        ),
    )


def _create_tts_run(repository: WorkflowRepository, run_id: str) -> WorkflowRun:
    sources = OVEN_SOURCES
    script_id, tts_id = sources.script_version_id, sources.tts_input_version_id
    authored = repository.get_script_text(script_id)
    rejected = prepare_spoken_text(authored, LITERAL_PROFILE)
    observed = repository.get_tts_input_version(tts_id)
    gate_facts = tuple(
        EvidenceInput(
            EvidenceRole.FACT,
            script_id,
            f"Spoken-text gate rejects the authored text unchanged: {issue.code}.",
            observed=issue.token,
        )
        for issue in rejected.issues
    )
    return repository.create(
        run_id=run_id,
        finding=QualityFinding(
            FailureKind.TTS_INPUT_COMPATIBILITY,
            "Reviewer Marcus Oyelaran (synthetic): at 0:03 the voice says "
            "“preheat the oven to four fifty F”.",
            0.9,
        ),
        sources=sources,
        observed_artifact_version_id=tts_id,
        evidence=gate_facts
        + (
            EvidenceInput(
                EvidenceRole.FACT,
                tts_id,
                f"The {TTS_MODEL} profile declared temperature support, so the "
                "corrected candidate reached the provider unnormalized.",
                observed=observed.spoken_text,
            ),
            EvidenceInput(
                EvidenceRole.INFERENCE,
                tts_id,
                "The declared capability is wrong for this model; spell out the "
                "temperature in provider-facing text and keep the authored script.",
            ),
            EvidenceInput(EvidenceRole.UNCERTAINTY, tts_id, SPOKEN_TEXT_NOTICE),
        ),
    )
