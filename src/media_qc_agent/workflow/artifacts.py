"""Immutable artifact versions, their content, and their exact lineage."""

import json
import sqlite3
from dataclasses import asdict

from media_qc_agent.domain.ids import (
    validate_artifact_version_id,
    validate_external_id,
    video_version_id,
)
from media_qc_agent.domain.models import ArtifactKind
from media_qc_agent.quality.captions import CaptionCue
from media_qc_agent.quality.environment import Environment, ScriptScene
from media_qc_agent.quality.spoken_text import (
    Notation,
    SpokenTextCapabilities,
    TtsInputVersion,
    UnsafeSpokenText,
    prepare_spoken_text,
)
from media_qc_agent.workflow.models import ArtifactVersion, VideoSources

_SOURCE_ORDER = (
    ArtifactKind.SCRIPT,
    ArtifactKind.TTS_INPUT,
    ArtifactKind.AVATAR,
    ArtifactKind.VOICE,
    ArtifactKind.VIDEO,
)


class ArtifactStore:
    """Each create method commits its own transaction; call it outside one.

    The insert methods write within the caller's transaction, and only for the
    outputs a workflow transition produces: generated video and captions.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def create_voice_version(self, version_id: str) -> ArtifactVersion:
        validate_artifact_version_id(version_id, ArtifactKind.VOICE)
        with self._connection:
            self._insert(version_id, ArtifactKind.VOICE, ())
        return self.get(version_id)

    def create_script_version(
        self, *, version_id: str, authored_text: str, scene: ScriptScene
    ) -> ArtifactVersion:
        validate_artifact_version_id(version_id, ArtifactKind.SCRIPT)
        if not authored_text.strip():
            raise ValueError("authored script text must not be blank")
        scene.validate_text(authored_text)
        with self._connection:
            self._insert(version_id, ArtifactKind.SCRIPT, ())
            self._connection.execute(
                """INSERT INTO script_versions
                   (version_id, authored_text, environment, evidence_phrase)
                   VALUES (?, ?, ?, ?)""",
                (version_id, authored_text, scene.environment, scene.evidence_phrase),
            )
        return self.get(version_id)

    def create_avatar_version(
        self, *, version_id: str, environment: Environment
    ) -> ArtifactVersion:
        validate_artifact_version_id(version_id, ArtifactKind.AVATAR)
        if not isinstance(environment, Environment):
            raise TypeError("avatar environment must be a declared Environment")
        with self._connection:
            self._insert(version_id, ArtifactKind.AVATAR, ())
            self._connection.execute(
                "INSERT INTO avatar_versions (version_id, environment) VALUES (?, ?)",
                (version_id, environment),
            )
        return self.get(version_id)

    def create_tts_input_version(
        self,
        *,
        version_id: str,
        script_version_id: str,
        capabilities: SpokenTextCapabilities,
        candidate_text: str | None = None,
    ) -> TtsInputVersion:
        """Persist only spoken text validated for one exact script and model."""

        validate_artifact_version_id(version_id, ArtifactKind.TTS_INPUT)
        if self.get(script_version_id).kind is not ArtifactKind.SCRIPT:
            raise ValueError("TTS input source must be a script version")
        result = prepare_spoken_text(
            self.script_text(script_version_id),
            capabilities,
            candidate_text=candidate_text,
        )
        if not result.notation_compatible:
            raise UnsafeSpokenText(result.issues)
        notation = sorted(item.value for item in capabilities.supported_notation)
        with self._connection:
            self._insert(
                version_id,
                ArtifactKind.TTS_INPUT,
                ((ArtifactKind.SCRIPT, script_version_id),),
            )
            self._connection.execute(
                """INSERT INTO tts_input_versions (
                       version_id, script_version_id, authored_text, candidate_text,
                       spoken_text, provider, model, supported_notation
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    version_id,
                    script_version_id,
                    result.authored_text,
                    result.candidate_text,
                    result.spoken_text,
                    capabilities.provider,
                    capabilities.model,
                    json.dumps(notation),
                ),
            )
        return self.tts_input(version_id)

    def create_synthetic_video_version(
        self, *, fixture_job_id: str, sources: VideoSources
    ) -> ArtifactVersion:
        """Seed an observed video for synthetic quality-gate fixtures."""

        validate_external_id(fixture_job_id, "fixture job")
        self.validate_sources(sources)
        version_id = video_version_id(fixture_job_id)
        with self._connection:
            self._insert(version_id, ArtifactKind.VIDEO, sources.dependencies())
        return self.get(version_id)

    def create_caption_version(
        self,
        *,
        version_id: str,
        video_version_id: str,
        cues: tuple[CaptionCue, ...] | None = None,
    ) -> ArtifactVersion:
        validate_artifact_version_id(version_id, ArtifactKind.CAPTIONS)
        if self.get(video_version_id).kind is not ArtifactKind.VIDEO:
            raise ValueError("caption source must be a video version")
        with self._connection:
            self.insert_captions(version_id, video_version_id, cues)
        return self.get(version_id)

    def insert_video(
        self, version_id: str, sources: VideoSources, external_job_id: str
    ) -> None:
        self._insert(
            version_id,
            ArtifactKind.VIDEO,
            sources.dependencies(),
            external_job_id=external_job_id,
        )

    def insert_captions(
        self,
        version_id: str,
        video_version_id: str,
        cues: tuple[CaptionCue, ...] | None,
    ) -> None:
        self._insert(
            version_id, ArtifactKind.CAPTIONS, ((ArtifactKind.VIDEO, video_version_id),)
        )
        if cues is not None:
            self._connection.execute(
                "INSERT INTO caption_contents (version_id, cues_json) VALUES (?, ?)",
                (version_id, json.dumps([asdict(cue) for cue in cues])),
            )

    def _insert(
        self,
        version_id: str,
        kind: ArtifactKind,
        sources: tuple[tuple[ArtifactKind, str], ...],
        *,
        external_job_id: str | None = None,
    ) -> None:
        self._connection.execute(
            "INSERT INTO artifact_versions (id, kind, external_job_id) VALUES (?, ?, ?)",
            (version_id, kind, external_job_id),
        )
        self._connection.executemany(
            """INSERT INTO artifact_dependencies (artifact_id, source_kind, source_id)
               VALUES (?, ?, ?)""",
            (
                (version_id, source_kind, source_id)
                for source_kind, source_id in sources
            ),
        )

    def get(self, version_id: str) -> ArtifactVersion:
        row = self._row("SELECT * FROM artifact_versions WHERE id = ?", version_id)
        dependencies = self._connection.execute(
            """SELECT source_kind, source_id FROM artifact_dependencies
               WHERE artifact_id = ?""",
            (version_id,),
        ).fetchall()
        sources = sorted(
            (
                (ArtifactKind(item["source_kind"]), item["source_id"])
                for item in dependencies
            ),
            key=lambda item: _SOURCE_ORDER.index(item[0]),
        )
        return ArtifactVersion(
            id=row["id"],
            kind=ArtifactKind(row["kind"]),
            source_versions=tuple(sources),
            external_job_id=row["external_job_id"],
            created_at=row["created_at"],
        )

    def script_text(self, version_id: str) -> str:
        row = self._row(
            "SELECT authored_text FROM script_versions WHERE version_id = ?", version_id
        )
        return str(row["authored_text"])

    def script_scene(self, version_id: str) -> ScriptScene:
        row = self._row(
            "SELECT environment, evidence_phrase FROM script_versions WHERE version_id = ?",
            version_id,
        )
        return ScriptScene(Environment(row["environment"]), row["evidence_phrase"])

    def avatar_environment(self, version_id: str) -> Environment:
        row = self._row(
            "SELECT environment FROM avatar_versions WHERE version_id = ?", version_id
        )
        return Environment(row["environment"])

    def tts_input(self, version_id: str) -> TtsInputVersion:
        row = self._row(
            "SELECT * FROM tts_input_versions WHERE version_id = ?", version_id
        )
        return TtsInputVersion(
            id=row["version_id"],
            script_version_id=row["script_version_id"],
            authored_text=row["authored_text"],
            candidate_text=row["candidate_text"],
            spoken_text=row["spoken_text"],
            capabilities=SpokenTextCapabilities(
                provider=row["provider"],
                model=row["model"],
                supported_notation=frozenset(
                    Notation(item) for item in json.loads(row["supported_notation"])
                ),
            ),
        )

    def caption_cues(self, version_id: str) -> tuple[CaptionCue, ...]:
        row = self._row(
            "SELECT cues_json FROM caption_contents WHERE version_id = ?", version_id
        )
        return tuple(CaptionCue(**item) for item in json.loads(row["cues_json"]))

    def video_sources(self, video_version_id: str) -> VideoSources:
        video = self.get(video_version_id)
        if video.kind is not ArtifactKind.VIDEO:
            raise ValueError(f"{video_version_id} is not a video version")
        source_ids = dict(video.source_versions)
        return VideoSources(
            script_version_id=source_ids[ArtifactKind.SCRIPT],
            tts_input_version_id=source_ids[ArtifactKind.TTS_INPUT],
            avatar_version_id=source_ids[ArtifactKind.AVATAR],
            voice_version_id=source_ids[ArtifactKind.VOICE],
        )

    def validate_sources(self, sources: VideoSources) -> None:
        for kind, version_id in sources.dependencies():
            if self.get(version_id).kind is not kind:
                raise ValueError(
                    f"{kind.value} source must reference a {kind.value} version"
                )
        self.validate_tts_binding(sources)

    def validate_tts_binding(self, sources: VideoSources) -> None:
        tts_input = self.tts_input(sources.tts_input_version_id)
        if tts_input.script_version_id != sources.script_version_id:
            raise ValueError("TTS input must derive from the selected script version")

    def _row(self, query: str, version_id: str) -> sqlite3.Row:
        row = self._connection.execute(query, (version_id,)).fetchone()
        if row is None:
            raise KeyError(version_id)
        return row
