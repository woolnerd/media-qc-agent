"""SQLite persistence for workflow runs and state transitions."""

import json
import sqlite3

from .domain import (
    ArtifactKind,
    ClarificationRequest,
    QualityFinding,
    RepairAction,
    RepairOption,
    RepairPlan,
)
from .ids import (
    validate_artifact_version_id,
    validate_external_id,
    validate_run_id,
    video_version_id,
)
from .planner import plan_repair, select_repair
from .spoken_text import (
    Notation,
    SpokenTextCapabilities,
    TtsInputVersion,
    UnsafeSpokenText,
    prepare_spoken_text,
)
from .workflow import (
    ArtifactVersion,
    ProviderEvent,
    ProviderEventDisposition,
    ProviderJob,
    VideoSources,
    WorkflowRun,
    WorkflowStatus,
    classify_completion,
)

_SOURCE_ORDER = (
    ArtifactKind.SCRIPT,
    ArtifactKind.TTS_INPUT,
    ArtifactKind.AVATAR,
    ArtifactKind.VOICE,
    ArtifactKind.VIDEO,
)


class WorkflowRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def initialize(self) -> None:
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_runs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                action TEXT,
                invalidates TEXT NOT NULL,
                requires_repair_input INTEGER NOT NULL,
                rationale TEXT NOT NULL,
                clarification_question TEXT,
                clarification_options TEXT,
                idempotency_key TEXT UNIQUE,
                external_job_id TEXT,
                script_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
                tts_input_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
                avatar_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
                voice_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
                active_video_version_id TEXT REFERENCES artifact_versions(id),
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_jobs (
                external_job_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES workflow_runs(id),
                idempotency_key TEXT NOT NULL UNIQUE,
                action TEXT NOT NULL,
                script_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
                tts_input_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
                avatar_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
                voice_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_versions (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                external_job_id TEXT UNIQUE,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS script_versions (
                version_id TEXT PRIMARY KEY REFERENCES artifact_versions(id),
                authored_text TEXT NOT NULL CHECK (trim(authored_text) <> '')
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tts_input_versions (
                version_id TEXT PRIMARY KEY REFERENCES artifact_versions(id),
                script_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
                authored_text TEXT NOT NULL,
                candidate_text TEXT NOT NULL,
                spoken_text TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                supported_notation TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_dependencies (
                artifact_id TEXT NOT NULL REFERENCES artifact_versions(id),
                source_kind TEXT NOT NULL,
                source_id TEXT NOT NULL REFERENCES artifact_versions(id),
                PRIMARY KEY (artifact_id, source_kind)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_events (
                external_event_id TEXT PRIMARY KEY,
                external_job_id TEXT NOT NULL REFERENCES provider_jobs(external_job_id),
                event_type TEXT NOT NULL CHECK (event_type = 'completed'),
                result_status TEXT NOT NULL,
                disposition TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """
        )
        self._connection.commit()

    def create_source_version(
        self, *, version_id: str, kind: ArtifactKind
    ) -> ArtifactVersion:
        if kind is ArtifactKind.SCRIPT:
            raise ValueError("script requires create_script_version")
        if kind is ArtifactKind.TTS_INPUT:
            raise ValueError("TTS input requires create_tts_input_version")
        if kind not in {
            ArtifactKind.AVATAR,
            ArtifactKind.VOICE,
        }:
            raise ValueError("source version must be avatar or voice")
        validate_artifact_version_id(version_id, kind)
        self._connection.execute(
            "INSERT INTO artifact_versions (id, kind) VALUES (?, ?)",
            (version_id, kind),
        )
        self._connection.commit()
        return self.get_artifact_version(version_id)

    def create_script_version(
        self, *, version_id: str, authored_text: str
    ) -> ArtifactVersion:
        """Keep approved synthetic script text immutable with its version ID."""

        validate_artifact_version_id(version_id, ArtifactKind.SCRIPT)
        if not authored_text.strip():
            raise ValueError("authored script text must not be blank")
        with self._connection:
            self._connection.execute(
                "INSERT INTO artifact_versions (id, kind) VALUES (?, ?)",
                (version_id, ArtifactKind.SCRIPT),
            )
            self._connection.execute(
                "INSERT INTO script_versions (version_id, authored_text) VALUES (?, ?)",
                (version_id, authored_text),
            )
        return self.get_artifact_version(version_id)

    def get_script_text(self, version_id: str) -> str:
        row = self._connection.execute(
            "SELECT authored_text FROM script_versions WHERE version_id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return str(row["authored_text"])

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
        script = self.get_artifact_version(script_version_id)
        if script.kind is not ArtifactKind.SCRIPT:
            raise ValueError("TTS input source must be a script version")
        result = prepare_spoken_text(
            self.get_script_text(script_version_id),
            capabilities,
            candidate_text=candidate_text,
        )
        if not result.safe:
            raise UnsafeSpokenText(result.issues)
        with self._connection:
            self._connection.execute(
                "INSERT INTO artifact_versions (id, kind) VALUES (?, ?)",
                (version_id, ArtifactKind.TTS_INPUT),
            )
            self._insert_dependencies(
                version_id, ((ArtifactKind.SCRIPT, script_version_id),)
            )
            self._connection.execute(
                """
                INSERT INTO tts_input_versions (
                    version_id, script_version_id, authored_text,
                    candidate_text, spoken_text, provider, model, supported_notation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    script_version_id,
                    result.authored_text,
                    result.candidate_text,
                    result.spoken_text,
                    capabilities.provider,
                    capabilities.model,
                    json.dumps(
                        sorted(item.value for item in capabilities.supported_notation)
                    ),
                ),
            )
        return self.get_tts_input_version(version_id)

    def get_tts_input_version(self, version_id: str) -> TtsInputVersion:
        row = self._connection.execute(
            "SELECT * FROM tts_input_versions WHERE version_id = ?", (version_id,)
        ).fetchone()
        if row is None:
            raise KeyError(version_id)
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

    def get_artifact_version(self, version_id: str) -> ArtifactVersion:
        row = self._connection.execute(
            "SELECT * FROM artifact_versions WHERE id = ?", (version_id,)
        ).fetchone()
        if row is None:
            raise KeyError(version_id)
        dependencies = self._connection.execute(
            "SELECT source_kind, source_id FROM artifact_dependencies WHERE artifact_id = ?",
            (version_id,),
        ).fetchall()
        sources = tuple(
            sorted(
                (
                    (ArtifactKind(item["source_kind"]), item["source_id"])
                    for item in dependencies
                ),
                key=lambda item: _SOURCE_ORDER.index(item[0]),
            )
        )
        return ArtifactVersion(
            id=row["id"],
            kind=ArtifactKind(row["kind"]),
            source_versions=sources,
            external_job_id=row["external_job_id"],
            created_at=row["created_at"],
        )

    def record_caption_version(
        self, *, version_id: str, video_version_id: str
    ) -> ArtifactVersion:
        validate_artifact_version_id(version_id, ArtifactKind.CAPTIONS)
        video = self.get_artifact_version(video_version_id)
        if video.kind is not ArtifactKind.VIDEO:
            raise ValueError("caption source must be a video version")
        with self._connection:
            self._connection.execute(
                "INSERT INTO artifact_versions (id, kind) VALUES (?, ?)",
                (version_id, ArtifactKind.CAPTIONS),
            )
            self._insert_dependencies(
                version_id, ((ArtifactKind.VIDEO, video_version_id),)
            )
        return self.get_artifact_version(version_id)

    def _insert_dependencies(
        self, artifact_id: str, sources: tuple[tuple[ArtifactKind, str], ...]
    ) -> None:
        self._connection.executemany(
            "INSERT INTO artifact_dependencies (artifact_id, source_kind, source_id) VALUES (?, ?, ?)",
            ((artifact_id, kind, source_id) for kind, source_id in sources),
        )

    def create(
        self, *, run_id: str, finding: QualityFinding, sources: VideoSources
    ) -> WorkflowRun:
        validate_run_id(run_id)
        self._validate_sources(sources)
        decision = plan_repair(finding)
        if isinstance(decision, ClarificationRequest):
            plan = None
            clarification = decision
            initial_status = WorkflowStatus.NEEDS_INPUT
        else:
            plan = decision
            clarification = None
            initial_status = (
                WorkflowStatus.NEEDS_REPAIR_INPUT
                if plan.requires_repair_input
                else WorkflowStatus.AWAITING_APPROVAL
            )

        self._connection.execute(
            """
            INSERT INTO workflow_runs (
                id,
                status,
                action,
                invalidates,
                requires_repair_input,
                rationale,
                clarification_question,
                clarification_options,
                idempotency_key,
                external_job_id,
                script_version_id, tts_input_version_id,
                avatar_version_id, voice_version_id
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                NULL, ?, ?, ?, ?
            )
            """,
            (
                run_id,
                initial_status,
                plan.action if plan else None,
                _encode_invalidates(plan.invalidates) if plan else "",
                plan.requires_repair_input if plan else False,
                plan.rationale if plan else "",
                clarification.question if clarification else None,
                _encode_options(clarification.options) if clarification else None,
                (
                    f"workflow-run:{run_id}:{plan.action}"
                    if plan and not plan.requires_repair_input
                    else None
                ),
                sources.script_version_id,
                sources.tts_input_version_id,
                sources.avatar_version_id,
                sources.voice_version_id,
            ),
        )
        self._connection.commit()
        return self.get(run_id)

    def _validate_sources(self, sources: VideoSources) -> None:
        for kind, version_id in sources.dependencies():
            artifact = self.get_artifact_version(version_id)
            if artifact.kind is not kind:
                raise ValueError(
                    f"{kind.value} source must reference a {kind.value} version"
                )
        self._validate_tts_binding(sources)

    def _validate_tts_binding(self, sources: VideoSources) -> None:
        tts_input = self.get_tts_input_version(sources.tts_input_version_id)
        if tts_input.script_version_id != sources.script_version_id:
            raise ValueError("TTS input must derive from the selected script version")

    def select_repair(self, run_id: str, action: RepairAction) -> WorkflowRun:
        """Record a branch choice while awaiting its replacement artifact."""

        current = self.get(run_id)
        if (
            current.status is not WorkflowStatus.NEEDS_INPUT
            or current.clarification is None
        ):
            raise ValueError("workflow is not awaiting clarification")
        plan = select_repair(current.clarification, action)
        result = self._connection.execute(
            """
            UPDATE workflow_runs
            SET status = ?, action = ?, invalidates = ?,
                requires_repair_input = ?, rationale = ?,
                clarification_question = NULL, clarification_options = NULL,
                idempotency_key = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ? AND status = ? AND action IS NULL
            """,
            (
                WorkflowStatus.NEEDS_REPAIR_INPUT,
                plan.action,
                _encode_invalidates(plan.invalidates),
                plan.requires_repair_input,
                plan.rationale,
                None,
                run_id,
                WorkflowStatus.NEEDS_INPUT,
            ),
        )
        self._connection.commit()
        if result.rowcount != 1:
            raise RuntimeError("workflow state changed during clarification")
        return self.get(run_id)

    def approve(self, run_id: str) -> WorkflowRun:
        current = self.get(run_id)
        if current.status is WorkflowStatus.AWAITING_APPROVAL:
            self._validate_tts_binding(current.sources)
        result = self._connection.execute(
            """
            UPDATE workflow_runs
            SET status = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ? AND status = ?
            """,
            (
                WorkflowStatus.READY,
                run_id,
                WorkflowStatus.AWAITING_APPROVAL,
            ),
        )
        self._connection.commit()
        if result.rowcount != 1:
            raise ValueError("workflow is missing or is not awaiting approval")
        return self.get(run_id)

    def bind_replacement(self, run_id: str, version_id: str) -> WorkflowRun:
        """Bind the exact new input required by a repair before approval."""

        current = self.get(run_id)
        if (
            current.status is not WorkflowStatus.NEEDS_REPAIR_INPUT
            or current.plan is None
        ):
            raise ValueError("workflow is not awaiting repair input")
        target = {
            RepairAction.REVISE_SCRIPT: (ArtifactKind.SCRIPT, "script_version_id"),
            RepairAction.REPAIR_TTS_INPUT: (
                ArtifactKind.TTS_INPUT,
                "tts_input_version_id",
            ),
            RepairAction.CHANGE_AVATAR: (ArtifactKind.AVATAR, "avatar_version_id"),
        }.get(current.plan.action)
        if target is None:
            raise ValueError("repair action has no replacement input")
        kind, column = target
        replacement = self.get_artifact_version(version_id)
        if replacement.kind is not kind:
            raise ValueError(f"replacement must be a {kind.value} version")
        if version_id in {value for _, value in current.sources.dependencies()}:
            raise ValueError("replacement must be a new source version")
        key = f"workflow-run:{run_id}:{current.plan.action}:replacement:{version_id}"
        updated = self._connection.execute(
            f"""
            UPDATE workflow_runs
            SET {column} = ?, status = ?, idempotency_key = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ? AND status = ?
            """,
            (
                version_id,
                WorkflowStatus.AWAITING_APPROVAL,
                key,
                run_id,
                WorkflowStatus.NEEDS_REPAIR_INPUT,
            ),
        )
        self._connection.commit()
        if updated.rowcount != 1:
            raise RuntimeError("workflow state changed during replacement binding")
        return self.get(run_id)

    def bind_tts_input(self, run_id: str, version_id: str) -> WorkflowRun:
        """Pair a revised script with its validated spoken text before approval."""

        current = self.get(run_id)
        if (
            current.status is not WorkflowStatus.AWAITING_APPROVAL
            or current.plan is None
            or current.plan.action is not RepairAction.REVISE_SCRIPT
        ):
            raise ValueError("workflow is not awaiting revised script TTS input")
        tts_input = self.get_tts_input_version(version_id)
        if tts_input.script_version_id != current.sources.script_version_id:
            raise ValueError("TTS input must derive from the selected script version")
        if version_id == current.sources.tts_input_version_id:
            raise ValueError("replacement TTS input must be a new version")
        key = (
            f"workflow-run:{run_id}:{current.plan.action}:"
            f"replacement:{current.sources.script_version_id}:tts:{version_id}"
        )
        updated = self._connection.execute(
            """
            UPDATE workflow_runs
            SET tts_input_version_id = ?, idempotency_key = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ? AND status = ? AND script_version_id = ?
            """,
            (
                version_id,
                key,
                run_id,
                WorkflowStatus.AWAITING_APPROVAL,
                current.sources.script_version_id,
            ),
        )
        self._connection.commit()
        if updated.rowcount != 1:
            raise RuntimeError("workflow state changed during TTS input binding")
        return self.get(run_id)

    def record_submission(self, *, run_id: str, external_job_id: str) -> WorkflowRun:
        """Record an accepted provider job without permitting ID replacement."""

        validate_external_id(external_job_id, "provider job")
        current = self.get(run_id)
        if current.status is WorkflowStatus.SUBMITTED:
            if current.external_job_id != external_job_id:
                raise ValueError("workflow already references another provider job")
            return current
        if current.status is not WorkflowStatus.READY:
            raise ValueError("workflow is not ready for provider submission")

        if current.plan is None or current.idempotency_key is None:
            raise ValueError("workflow has no executable repair plan")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO provider_jobs (
                    external_job_id, run_id, idempotency_key, action,
                    script_version_id, tts_input_version_id,
                    avatar_version_id, voice_version_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    external_job_id,
                    run_id,
                    current.idempotency_key,
                    current.plan.action,
                    current.sources.script_version_id,
                    current.sources.tts_input_version_id,
                    current.sources.avatar_version_id,
                    current.sources.voice_version_id,
                ),
            )
            result = self._connection.execute(
                """
                UPDATE workflow_runs
                SET status = ?, external_job_id = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status = ?
                """,
                (
                    WorkflowStatus.SUBMITTED,
                    external_job_id,
                    run_id,
                    WorkflowStatus.READY,
                ),
            )
            if result.rowcount != 1:
                raise RuntimeError("workflow state changed during provider submission")
        return self.get(run_id)

    def record_completion(
        self, *, external_job_id: str, external_event_id: str
    ) -> WorkflowRun:
        """Audit a completion and advance only the currently submitted job."""

        validate_external_id(external_job_id, "provider job")
        validate_external_id(external_event_id, "provider event")
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            job = self.get_provider_job(external_job_id)
            run = self.get(job.run_id)
            disposition, reason = classify_completion(
                job_is_active=run.external_job_id == external_job_id,
                status=run.status,
            )
            inserted = self._connection.execute(
                """
                INSERT OR IGNORE INTO provider_events (
                    external_event_id, external_job_id, event_type, result_status,
                    disposition, reason
                ) VALUES (?, ?, 'completed', ?, ?, ?)
                """,
                (
                    external_event_id,
                    external_job_id,
                    WorkflowStatus.SUCCEEDED,
                    disposition,
                    reason,
                ),
            )
            if inserted.rowcount == 0:
                existing = self.get_provider_event(external_event_id)
                if existing.external_job_id != external_job_id:
                    raise ValueError("event identifier belongs to another provider job")
                return self.get(job.run_id)

            if disposition is ProviderEventDisposition.APPLIED:
                generated_video_id = video_version_id(external_job_id)
                self._connection.execute(
                    "INSERT INTO artifact_versions (id, kind, external_job_id) VALUES (?, ?, ?)",
                    (generated_video_id, ArtifactKind.VIDEO, external_job_id),
                )
                self._insert_dependencies(
                    generated_video_id, job.sources.dependencies()
                )
                updated = self._connection.execute(
                    """
                    UPDATE workflow_runs
                    SET status = ?, active_video_version_id = ?,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ? AND status = ? AND external_job_id = ?
                    """,
                    (
                        WorkflowStatus.SUCCEEDED,
                        generated_video_id,
                        job.run_id,
                        WorkflowStatus.SUBMITTED,
                        external_job_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise RuntimeError(
                        "workflow state changed during provider completion"
                    )
        return self.get(job.run_id)

    def request_retry(self, run_id: str) -> WorkflowRun:
        """Supersede an in-flight job and require approval for a new attempt."""

        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.get(run_id)
            if (
                current.status
                not in {WorkflowStatus.SUBMITTED, WorkflowStatus.SUCCEEDED}
                or current.plan is None
            ):
                raise ValueError("workflow has no submitted job to retry")
            attempts = self._connection.execute(
                "SELECT COUNT(*) FROM provider_jobs WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            key = f"workflow-run:{run_id}:{current.plan.action}:retry-{attempts}"
            updated = self._connection.execute(
                """
                UPDATE workflow_runs
                SET status = ?, idempotency_key = ?, external_job_id = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status = ? AND external_job_id = ?
                """,
                (
                    WorkflowStatus.AWAITING_APPROVAL,
                    key,
                    run_id,
                    current.status,
                    current.external_job_id,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("workflow state changed during retry request")
        return self.get(run_id)

    def get_provider_job(self, external_job_id: str) -> ProviderJob:
        row = self._connection.execute(
            "SELECT * FROM provider_jobs WHERE external_job_id = ?",
            (external_job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(external_job_id)
        return ProviderJob(
            external_job_id=row["external_job_id"],
            run_id=row["run_id"],
            idempotency_key=row["idempotency_key"],
            action=row["action"],
            sources=VideoSources(
                script_version_id=row["script_version_id"],
                tts_input_version_id=row["tts_input_version_id"],
                avatar_version_id=row["avatar_version_id"],
                voice_version_id=row["voice_version_id"],
            ),
            created_at=row["created_at"],
        )

    def get_provider_event(self, external_event_id: str) -> ProviderEvent:
        row = self._connection.execute(
            "SELECT * FROM provider_events WHERE external_event_id = ?",
            (external_event_id,),
        ).fetchone()
        if row is None:
            raise KeyError(external_event_id)
        return ProviderEvent(
            external_event_id=row["external_event_id"],
            external_job_id=row["external_job_id"],
            event_type=row["event_type"],
            result_status=WorkflowStatus(row["result_status"]),
            disposition=ProviderEventDisposition(row["disposition"]),
            reason=row["reason"],
            created_at=row["created_at"],
        )

    def get(self, run_id: str) -> WorkflowRun:
        row = self._connection.execute(
            "SELECT * FROM workflow_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)

        invalidates = frozenset(
            ArtifactKind(value) for value in row["invalidates"].split(",") if value
        )
        clarification = None
        if row["clarification_question"] is not None:
            clarification = ClarificationRequest(
                question=row["clarification_question"],
                options=_decode_options(row["clarification_options"]),
            )
        return WorkflowRun(
            id=row["id"],
            status=WorkflowStatus(row["status"]),
            plan=(
                RepairPlan(
                    action=RepairAction(row["action"]),
                    invalidates=invalidates,
                    requires_repair_input=bool(row["requires_repair_input"]),
                    rationale=row["rationale"],
                )
                if row["action"] is not None
                else None
            ),
            clarification=clarification,
            idempotency_key=row["idempotency_key"],
            external_job_id=row["external_job_id"],
            sources=VideoSources(
                script_version_id=row["script_version_id"],
                tts_input_version_id=row["tts_input_version_id"],
                avatar_version_id=row["avatar_version_id"],
                voice_version_id=row["voice_version_id"],
            ),
            active_video_version_id=row["active_video_version_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _encode_invalidates(invalidates: frozenset[ArtifactKind]) -> str:
    return ",".join(sorted(artifact.value for artifact in invalidates))


def _encode_options(options: tuple[RepairOption, ...]) -> str:
    return json.dumps(
        [
            {
                "action": option.action.value,
                "invalidates": sorted(
                    artifact.value for artifact in option.invalidates
                ),
                "rationale": option.rationale,
            }
            for option in options
        ]
    )


def _decode_options(value: str) -> tuple[RepairOption, ...]:
    return tuple(
        RepairOption(
            action=RepairAction(option["action"]),
            invalidates=frozenset(
                ArtifactKind(artifact) for artifact in option["invalidates"]
            ),
            rationale=option["rationale"],
        )
        for option in json.loads(value)
    )
