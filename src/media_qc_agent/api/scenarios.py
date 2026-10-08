"""Fixed, explicitly illustrative scenarios; no live inference or provider calls.

Every person, product, script, reviewer note, and provider profile below is
invented for this demo. "Halden Home" is a fictional appliance brand.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

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


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    demo_notice: str = DEMO_NOTICE


SCENARIOS = (
    Scenario("weak-script", "Thermostat setup script gives vague steps"),
    Scenario("environment-mismatch", "Oven script assigned to an office avatar"),
    Scenario("tts-input", "Reviewer hears 450°F as “four fifty F”"),
    Scenario("jerky-video", "Two motion spikes within one shot"),
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
# A slow push-in at 2-4 px/frame with two same-shot spikes at frames 4 and 8;
# each spike rises and falls, so the signal reports four abrupt changes.
MOTION_PX = (3, 3, 4, 3, 29, 4, 3, 3, 31, 4, 3, 3)


class FixtureDrift(RuntimeError):
    def __init__(self, version_id: str) -> None:
        super().__init__(
            f"Demo fixture {version_id} differs from the current seed; delete this "
            "demo database and its .provider.sqlite3 ledger, then restart."
        )


@dataclass(frozen=True)
class _Fixture:
    version_id: str
    read: Callable[[], object]
    create: Callable[[], object]
    expected: object


SCRIPTS = (
    ("script-api-original", THERMOSTAT_DRAFT, ScriptScene(Environment.NEUTRAL)),
    ("script-api-revised", THERMOSTAT_REVISED, ScriptScene(Environment.NEUTRAL)),
    ("script-api-kitchen", OVEN_PROMPT, ScriptScene(Environment.KITCHEN, "oven")),
    ("script-api-oven", OVEN_SHORTHAND, ScriptScene(Environment.KITCHEN, "oven")),
)
TTS_INPUTS = (
    ("tts-api-original", "script-api-original", LITERAL_PROFILE, None),
    ("tts-api-revised", "script-api-revised", LITERAL_PROFILE, None),
    ("tts-api-kitchen", "script-api-kitchen", LITERAL_PROFILE, None),
    ("tts-api-oven", "script-api-oven", CLAIMED_PROFILE, OVEN_CANDIDATE),
    ("tts-api-replacement", "script-api-oven", LITERAL_PROFILE, OVEN_CANDIDATE),
)
CAPTION_CUES = (CaptionCue(0, 4200, THERMOSTAT_STEP),)


def seed_scenarios(repository: WorkflowRepository) -> None:
    """Seed the demo database once; refuse to mix fixtures from another seed.

    Every existing fixture is compared before any missing one is created, so a
    stale database is rejected without gaining rows from the current seed.
    """

    missing: list[_Fixture] = []
    for fixture in _fixtures(repository):
        try:
            stored = fixture.read()
        except KeyError:
            missing.append(fixture)
            continue
        if stored != fixture.expected:
            raise FixtureDrift(fixture.version_id)
    for fixture in missing:
        fixture.create()


def _fixtures(repository: WorkflowRepository) -> tuple[_Fixture, ...]:
    return (
        *(
            _Fixture(
                version_id,
                partial(_script_identity, repository, version_id),
                partial(
                    repository.create_script_version,
                    version_id=version_id,
                    authored_text=text,
                    scene=scene,
                ),
                (text, scene),
            )
            for version_id, text, scene in SCRIPTS
        ),
        *_tts_fixtures(repository),
        *(
            _Fixture(
                f"avatar-api-{environment.value}",
                partial(
                    repository.get_avatar_environment, f"avatar-api-{environment.value}"
                ),
                partial(
                    repository.create_avatar_version,
                    version_id=f"avatar-api-{environment.value}",
                    environment=environment,
                ),
                environment,
            )
            for environment in Environment
        ),
        _Fixture(
            "voice-api-demo",
            lambda: repository.get_artifact_version("voice-api-demo").kind,
            partial(
                repository.create_source_version,
                version_id="voice-api-demo",
                kind=ArtifactKind.VOICE,
            ),
            ArtifactKind.VOICE,
        ),
        _Fixture(
            "video:api-observed",
            lambda: dict(
                repository.get_artifact_version("video:api-observed").source_versions
            ),
            partial(
                repository.create_synthetic_video_version,
                fixture_job_id="api-observed",
                sources=APPROVED_SOURCES,
            ),
            dict(APPROVED_SOURCES.dependencies()),
        ),
        _Fixture(
            "caption-api-observed",
            partial(repository.get_caption_cues, "caption-api-observed"),
            partial(
                repository.record_caption_version,
                version_id="caption-api-observed",
                video_version_id="video:api-observed",
                cues=CAPTION_CUES,
            ),
            CAPTION_CUES,
        ),
    )


def _tts_fixtures(repository: WorkflowRepository) -> tuple[_Fixture, ...]:
    texts = {version_id: text for version_id, text, _ in SCRIPTS}
    return tuple(
        _Fixture(
            version_id,
            partial(_tts_identity, repository, version_id),
            partial(
                repository.create_tts_input_version,
                version_id=version_id,
                script_version_id=script_id,
                capabilities=profile,
                candidate_text=candidate,
            ),
            (
                script_id,
                profile,
                prepare_spoken_text(
                    texts[script_id], profile, candidate_text=candidate
                ).spoken_text,
            ),
        )
        for version_id, script_id, profile, candidate in TTS_INPUTS
    )


def _script_identity(
    repository: WorkflowRepository, version_id: str
) -> tuple[str, ScriptScene]:
    return (
        repository.get_script_text(version_id),
        repository.get_script_scene(version_id),
    )


def _tts_identity(
    repository: WorkflowRepository, version_id: str
) -> tuple[str, SpokenTextCapabilities, str]:
    """Spoken text covers the candidate and the normalizer that produced it."""

    stored = repository.get_tts_input_version(version_id)
    return stored.script_version_id, stored.capabilities, stored.spoken_text


def create_scenario_run(
    repository: WorkflowRepository, scenario_id: str, run_id: str
) -> WorkflowRun:
    """Raise KeyError for an unknown scenario rather than guessing one."""

    builders: dict[str, Callable[[], WorkflowRun | None]] = {
        "weak-script": partial(_create_weak_script_run, repository, run_id),
        "environment-mismatch": partial(
            repository.create_environment_run, run_id=run_id, sources=KITCHEN_SOURCES
        ),
        "tts-input": partial(_create_tts_run, repository, run_id),
        "jerky-video": partial(
            repository.create_visual_quality_run,
            run_id=run_id,
            video_version_id="video:api-observed",
            samples=tuple(
                MotionSample(index, displacement)
                for index, displacement in enumerate(MOTION_PX)
            ),
        ),
        "caption-format": partial(
            repository.create_caption_quality_run,
            run_id=run_id,
            caption_version_id="caption-api-observed",
        ),
    }
    result = builders[scenario_id]()
    assert result is not None, "synthetic scenario must produce its expected finding"
    return result


def _create_weak_script_run(repository: WorkflowRepository, run_id: str) -> WorkflowRun:
    script_id = SOURCES.script_version_id
    return repository.create(
        run_id=run_id,
        finding=QualityFinding(
            FailureKind.SCRIPT_QUALITY,
            "Synthetic reviewer (script desk): the setup step says “press the "
            "thing, then the other thing”, so viewers cannot tell which control "
            "to use.",
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
    """Stored records are facts; the reported mispronunciation is not."""

    sources = OVEN_SOURCES
    script_id, tts_id = sources.script_version_id, sources.tts_input_version_id
    rejected = prepare_spoken_text(
        repository.get_script_text(script_id), LITERAL_PROFILE
    )
    observed = repository.get_tts_input_version(tts_id)
    gate_facts = tuple(
        EvidenceInput(
            EvidenceRole.FACT,
            script_id,
            "The authored shorthand needed a corrected candidate; the spoken-text "
            f"gate rejects it unchanged as {issue.code}.",
            observed=issue.token,
        )
        for issue in rejected.issues
    )
    return repository.create(
        run_id=run_id,
        finding=QualityFinding(
            FailureKind.TTS_INPUT_COMPATIBILITY,
            "Synthetic reviewer (voice QA): at 0:03 the voice seems to say "
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
                f"Prepared for {TTS_MODEL} under a profile that declares "
                "temperature support, so the spoken text keeps the °F symbol form.",
                observed=observed.spoken_text,
            ),
            EvidenceInput(
                EvidenceRole.INFERENCE,
                tts_id,
                "If the reported “four fifty F” is accurate, the profile overstates "
                f"{TTS_MODEL}; spell out the temperature in provider-facing text and "
                "keep the authored script.",
            ),
            EvidenceInput(
                EvidenceRole.UNCERTAINTY,
                tts_id,
                "No audio exists in this demo; the reported pronunciation is a "
                "fixture assumption, not an observed provider result.",
            ),
            EvidenceInput(EvidenceRole.UNCERTAINTY, tts_id, SPOKEN_TEXT_NOTICE),
        ),
    )
