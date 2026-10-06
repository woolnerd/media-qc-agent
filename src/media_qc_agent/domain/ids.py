"""Small, readable ID contract for the synthetic workflow."""

import hashlib
import re

from media_qc_agent.domain.models import ArtifactKind

_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_ARTIFACT_PREFIX = {
    ArtifactKind.SCRIPT: "script-",
    ArtifactKind.TTS_INPUT: "tts-",
    ArtifactKind.AVATAR: "avatar-",
    ArtifactKind.VOICE: "voice-",
    ArtifactKind.CAPTIONS: "caption-",
}


def reference(value: str | None) -> str | None:
    """SHA-256 correlation reference shared by every trace; not anonymization."""

    return hashlib.sha256(value.encode()).hexdigest() if value is not None else None


def validate_run_id(run_id: str) -> None:
    """Run IDs are caller-chosen lowercase slugs with no delimiters."""

    if _SLUG.fullmatch(run_id) is None:
        raise ValueError("run ID must be a lowercase hyphenated slug")


def validate_artifact_version_id(version_id: str, kind: ArtifactKind) -> None:
    """Caller-owned artifact IDs carry a kind prefix and a slug suffix."""

    if kind is ArtifactKind.VIDEO:
        raise ValueError("video version IDs are generated from provider job IDs")
    prefix = _ARTIFACT_PREFIX[kind]
    suffix = version_id.removeprefix(prefix)
    if not version_id.startswith(prefix) or _SLUG.fullmatch(suffix) is None:
        raise ValueError(f"{kind.value} version ID must use the {prefix}<slug> format")


def validate_external_id(value: str, label: str) -> None:
    """Provider-owned job and event IDs are opaque but cannot be blank."""

    if not value.strip():
        raise ValueError(f"{label} ID must not be blank")


def video_version_id(external_job_id: str) -> str:
    """Give one provider job one deterministic video version identity."""

    validate_external_id(external_job_id, "provider job")
    return f"video:{external_job_id}"
