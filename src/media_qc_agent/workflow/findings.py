"""Check that a finding and its evidence belong to the lineage under repair."""

from media_qc_agent.domain.evidence import EvidenceInput
from media_qc_agent.domain.models import ArtifactKind, FailureKind
from media_qc_agent.workflow.artifacts import ArtifactStore
from media_qc_agent.workflow.models import ArtifactVersion, VideoSources

_OBSERVED_KIND = {
    FailureKind.SCRIPT_QUALITY: ArtifactKind.SCRIPT,
    FailureKind.TTS_INPUT_COMPATIBILITY: ArtifactKind.TTS_INPUT,
    FailureKind.ENVIRONMENT_MISMATCH: ArtifactKind.SCRIPT,
    FailureKind.CAPTION_FORMAT: ArtifactKind.CAPTIONS,
    FailureKind.VISUAL_QUALITY: ArtifactKind.VIDEO,
}


def validate_video_source(
    artifacts: ArtifactStore, video_version_id: str, sources: VideoSources
) -> None:
    video = artifacts.get(video_version_id)
    if video.kind is not ArtifactKind.VIDEO:
        raise ValueError("finding source must be a video version")
    if video.source_versions != sources.dependencies():
        raise ValueError("finding video must match selected source versions")


def observed_artifact_id(
    artifacts: ArtifactStore,
    kind: FailureKind,
    sources: VideoSources,
    video_version_id: str | None,
    explicit_id: str | None,
) -> str:
    """Resolve the exact version a finding observed, defaulting from its kind."""

    expected_kind = _OBSERVED_KIND[kind]
    inferred = {
        ArtifactKind.SCRIPT: sources.script_version_id,
        ArtifactKind.TTS_INPUT: sources.tts_input_version_id,
        ArtifactKind.VIDEO: video_version_id,
    }.get(expected_kind)
    observed_id = explicit_id or inferred
    if observed_id is None:
        raise ValueError("finding requires an exact observed artifact version")
    observed = artifacts.get(observed_id)
    if observed.kind is not expected_kind:
        raise ValueError("observed artifact has the wrong kind for this finding")
    _validate_observed_lineage(artifacts, observed, inferred, sources, video_version_id)
    return observed_id


def _validate_observed_lineage(
    artifacts: ArtifactStore,
    observed: ArtifactVersion,
    inferred: str | None,
    sources: VideoSources,
    video_version_id: str | None,
) -> None:
    if observed.kind is ArtifactKind.VIDEO:
        validate_video_source(artifacts, observed.id, sources)
        if video_version_id is not None and observed.id != video_version_id:
            raise ValueError("observed artifact must match the active video version")
    elif observed.kind is ArtifactKind.CAPTIONS:
        if observed.source_versions != ((ArtifactKind.VIDEO, video_version_id),):
            raise ValueError("observed artifact must derive from the active video")
    elif observed.id != inferred:
        raise ValueError("observed artifact must match the selected source version")


def validate_evidence(
    evidence: tuple[EvidenceInput, ...],
    sources: VideoSources,
    observed_id: str,
    video_version_id: str | None,
) -> None:
    allowed = {version_id for _, version_id in sources.dependencies()}
    allowed.add(observed_id)
    if video_version_id is not None:
        allowed.add(video_version_id)
    for item in evidence:
        if item.artifact_version_id not in allowed:
            raise ValueError("evidence artifact must belong to the finding lineage")
