"""SQLite persistence for workflow runs and state transitions."""

import json
import sqlite3
import time
from collections.abc import Callable

from media_qc_agent.domain.evidence import (
    EvidenceInput,
    EvidenceRecord,
    EvidenceRole,
    QualityFindingRecord,
)
from media_qc_agent.domain.ids import (
    validate_artifact_version_id,
    validate_external_id,
    validate_run_id,
    video_version_id,
)
from media_qc_agent.domain.models import (
    ArtifactKind,
    ClarificationRequest,
    FailureKind,
    QualityFinding,
    RepairAction,
    RepairOption,
    RepairPlan,
)
from media_qc_agent.domain.planner import plan_repair, select_repair
from media_qc_agent.quality.captions import (
    CaptionCue,
    UnsafeCaptions,
    validate_captions,
)
from media_qc_agent.quality.environment import (
    Environment,
    EnvironmentCheck,
    ScriptScene,
    check_script_avatar_compatibility,
)
from media_qc_agent.quality.spoken_text import (
    Notation,
    SpokenTextCapabilities,
    TtsInputVersion,
    UnsafeSpokenText,
    prepare_spoken_text,
)
from media_qc_agent.quality.visual_quality import (
    MotionSample,
    VisualSignalCheck,
    check_jerky_video,
)
from media_qc_agent.workflow.models import (
    ArtifactVersion,
    PlanApproval,
    ProviderEvent,
    ProviderEventDisposition,
    ProviderJob,
    RepairPlanVersion,
    VideoSources,
    WorkflowRun,
    WorkflowStatus,
    classify_completion,
    has_current_approval,
)
from media_qc_agent.workflow.plan_versions import (
    decode_plan_version,
    encode_plan_version,
    validate_plan_revision,
)
from media_qc_agent.workflow.worker_models import SubmissionLease
from media_qc_agent.workflow.worker_queue import (
    initialize_worker_schema,
    require_submission_lease,
)

_SOURCE_ORDER = (
    ArtifactKind.SCRIPT,
    ArtifactKind.TTS_INPUT,
    ArtifactKind.AVATAR,
    ArtifactKind.VOICE,
    ArtifactKind.VIDEO,
)


class WorkflowRepository:
    def __init__(
        self, connection: sqlite3.Connection, *, clock: Callable[[], float] = time.time
    ) -> None:
        self._connection = connection
        self._clock = clock
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
                active_caption_version_id TEXT REFERENCES artifact_versions(id),
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
                plan_version_id TEXT REFERENCES repair_plan_versions(id),
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
                authored_text TEXT NOT NULL CHECK (trim(authored_text) <> ''),
                environment TEXT NOT NULL,
                evidence_phrase TEXT
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS avatar_versions (
                version_id TEXT PRIMARY KEY REFERENCES artifact_versions(id),
                environment TEXT NOT NULL
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
            CREATE TABLE IF NOT EXISTS caption_contents (
                version_id TEXT PRIMARY KEY REFERENCES artifact_versions(id),
                cues_json TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS quality_findings (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL UNIQUE REFERENCES workflow_runs(id),
                artifact_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
                kind TEXT NOT NULL,
                explanation TEXT NOT NULL,
                confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS quality_evidence (
                id TEXT PRIMARY KEY,
                finding_id TEXT NOT NULL REFERENCES quality_findings(id),
                ordinal INTEGER NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('fact', 'inference', 'uncertainty')),
                artifact_version_id TEXT NOT NULL REFERENCES artifact_versions(id),
                statement TEXT NOT NULL CHECK (trim(statement) <> ''),
                observed TEXT,
                limit_value TEXT,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                UNIQUE (finding_id, ordinal)
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
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS repair_plan_versions (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES workflow_runs(id),
                revision INTEGER NOT NULL,
                snapshot TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                UNIQUE (run_id, revision)
            );
            CREATE TABLE IF NOT EXISTS plan_approvals (
                plan_version_id TEXT PRIMARY KEY REFERENCES repair_plan_versions(id),
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            CREATE TRIGGER IF NOT EXISTS immutable_plan_update
                BEFORE UPDATE ON repair_plan_versions BEGIN
                SELECT RAISE(ABORT, 'plan versions are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_plan_delete
                BEFORE DELETE ON repair_plan_versions BEGIN
                SELECT RAISE(ABORT, 'plan versions are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_approval_update
                BEFORE UPDATE ON plan_approvals BEGIN
                SELECT RAISE(ABORT, 'approvals are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_approval_delete
                BEFORE DELETE ON plan_approvals BEGIN
                SELECT RAISE(ABORT, 'approvals are immutable'); END;
            """
        )
        provider_columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(provider_jobs)")
        }
        if "plan_version_id" not in provider_columns:
            self._connection.execute(
                "ALTER TABLE provider_jobs ADD COLUMN plan_version_id TEXT REFERENCES repair_plan_versions(id)"
            )
        self._upgrade_unversioned_plans()
        initialize_worker_schema(self._connection)
        self._connection.commit()

    def create_source_version(
        self, *, version_id: str, kind: ArtifactKind
    ) -> ArtifactVersion:
        if kind is ArtifactKind.SCRIPT:
            raise ValueError("script requires create_script_version")
        if kind is ArtifactKind.TTS_INPUT:
            raise ValueError("TTS input requires create_tts_input_version")
        if kind is ArtifactKind.AVATAR:
            raise ValueError("avatar requires create_avatar_version")
        if kind is not ArtifactKind.VOICE:
            raise ValueError("source version must be a voice")
        validate_artifact_version_id(version_id, kind)
        self._connection.execute(
            "INSERT INTO artifact_versions (id, kind) VALUES (?, ?)",
            (version_id, kind),
        )
        self._connection.commit()
        return self.get_artifact_version(version_id)

    def create_script_version(
        self, *, version_id: str, authored_text: str, scene: ScriptScene
    ) -> ArtifactVersion:
        """Keep approved synthetic script text immutable with its version ID."""

        validate_artifact_version_id(version_id, ArtifactKind.SCRIPT)
        if not authored_text.strip():
            raise ValueError("authored script text must not be blank")
        scene.validate_text(authored_text)
        with self._connection:
            self._connection.execute(
                "INSERT INTO artifact_versions (id, kind) VALUES (?, ?)",
                (version_id, ArtifactKind.SCRIPT),
            )
            self._connection.execute(
                """
                INSERT INTO script_versions
                    (version_id, authored_text, environment, evidence_phrase)
                VALUES (?, ?, ?, ?)
                """,
                (
                    version_id,
                    authored_text,
                    scene.environment,
                    scene.evidence_phrase,
                ),
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

    def get_script_scene(self, version_id: str) -> ScriptScene:
        row = self._connection.execute(
            "SELECT environment, evidence_phrase FROM script_versions WHERE version_id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return ScriptScene(Environment(row["environment"]), row["evidence_phrase"])

    def create_avatar_version(
        self, *, version_id: str, environment: Environment
    ) -> ArtifactVersion:
        validate_artifact_version_id(version_id, ArtifactKind.AVATAR)
        if not isinstance(environment, Environment):
            raise TypeError("avatar environment must be a declared Environment")
        with self._connection:
            self._connection.execute(
                "INSERT INTO artifact_versions (id, kind) VALUES (?, ?)",
                (version_id, ArtifactKind.AVATAR),
            )
            self._connection.execute(
                "INSERT INTO avatar_versions (version_id, environment) VALUES (?, ?)",
                (version_id, environment),
            )
        return self.get_artifact_version(version_id)

    def get_avatar_environment(self, version_id: str) -> Environment:
        row = self._connection.execute(
            "SELECT environment FROM avatar_versions WHERE version_id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return Environment(row["environment"])

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
        if not result.notation_compatible:
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

    def create_synthetic_video_version(
        self, *, fixture_job_id: str, sources: VideoSources
    ) -> ArtifactVersion:
        """Seed an observed video for synthetic quality-gate fixtures."""

        validate_external_id(fixture_job_id, "fixture job")
        self._validate_sources(sources)
        version_id = video_version_id(fixture_job_id)
        with self._connection:
            self._connection.execute(
                "INSERT INTO artifact_versions (id, kind) VALUES (?, ?)",
                (version_id, ArtifactKind.VIDEO),
            )
            self._insert_dependencies(version_id, sources.dependencies())
        return self.get_artifact_version(version_id)

    def record_caption_version(
        self,
        *,
        version_id: str,
        video_version_id: str,
        cues: tuple[CaptionCue, ...] | None = None,
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
            if cues is not None:
                self._connection.execute(
                    "INSERT INTO caption_contents (version_id, cues_json) VALUES (?, ?)",
                    (version_id, _encode_cues(cues)),
                )
        return self.get_artifact_version(version_id)

    def get_caption_cues(self, version_id: str) -> tuple[CaptionCue, ...]:
        row = self._connection.execute(
            "SELECT cues_json FROM caption_contents WHERE version_id = ?", (version_id,)
        ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return tuple(CaptionCue(**item) for item in json.loads(row["cues_json"]))

    def record_caption_repair(
        self,
        *,
        run_id: str,
        version_id: str,
        cues: tuple[CaptionCue, ...],
        expected_plan_version_id: str | None = None,
    ) -> ArtifactVersion:
        """Promote a validated caption version without a provider video job."""

        validate_artifact_version_id(version_id, ArtifactKind.CAPTIONS)
        checked = validate_captions(cues)
        if not checked.valid:
            raise UnsafeCaptions(checked.evidence)
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            run = self.get(run_id)
            self._require_optional_version(run, expected_plan_version_id)
            if (
                run.status is not WorkflowStatus.READY
                or run.plan is None
                or run.plan.action is not RepairAction.REPAIR_CAPTIONS
                or run.active_video_version_id is None
            ):
                raise ValueError("workflow is not ready for caption repair")
            if not has_current_approval(run):
                raise ValueError("workflow has no current plan-version approval")
            self._connection.execute(
                "INSERT INTO artifact_versions (id, kind) VALUES (?, ?)",
                (version_id, ArtifactKind.CAPTIONS),
            )
            self._insert_dependencies(
                version_id, ((ArtifactKind.VIDEO, run.active_video_version_id),)
            )
            self._connection.execute(
                "INSERT INTO caption_contents (version_id, cues_json) VALUES (?, ?)",
                (version_id, _encode_cues(cues)),
            )
            updated = self._connection.execute(
                """
                UPDATE workflow_runs
                SET status = ?, active_caption_version_id = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status = ? AND active_video_version_id = ?
                """,
                (
                    WorkflowStatus.SUCCEEDED,
                    version_id,
                    run_id,
                    WorkflowStatus.READY,
                    run.active_video_version_id,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("workflow state changed during caption repair")
        return self.get_artifact_version(version_id)

    def _insert_dependencies(
        self, artifact_id: str, sources: tuple[tuple[ArtifactKind, str], ...]
    ) -> None:
        self._connection.executemany(
            "INSERT INTO artifact_dependencies (artifact_id, source_kind, source_id) VALUES (?, ?, ?)",
            ((artifact_id, kind, source_id) for kind, source_id in sources),
        )

    def create(
        self,
        *,
        run_id: str,
        finding: QualityFinding,
        sources: VideoSources,
        video_version_id: str | None = None,
        observed_artifact_version_id: str | None = None,
        evidence: tuple[EvidenceInput, ...] | None = None,
    ) -> WorkflowRun:
        validate_run_id(run_id)
        self._validate_sources(sources)
        if finding.kind is FailureKind.CAPTION_FORMAT and video_version_id is None:
            raise ValueError("caption finding requires an existing video version")
        if video_version_id is not None:
            self._validate_video_source(video_version_id, sources)
        observed_id = self._observed_artifact_id(
            finding.kind, sources, video_version_id, observed_artifact_version_id
        )
        evidence = evidence or (
            EvidenceInput(EvidenceRole.INFERENCE, observed_id, finding.explanation),
            EvidenceInput(
                EvidenceRole.UNCERTAINTY,
                observed_id,
                "No independent validator evidence was supplied for this finding.",
            ),
        )
        self._validate_evidence(evidence, sources, observed_id, video_version_id)
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

        with self._connection:
            self._insert_workflow_run(
                run_id, sources, video_version_id, plan, clarification, initial_status
            )
            finding_id = f"finding-{run_id}"
            self._connection.execute(
                """
                INSERT INTO quality_findings
                    (id, run_id, artifact_version_id, kind, explanation, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    finding_id,
                    run_id,
                    observed_id,
                    finding.kind,
                    finding.explanation,
                    finding.confidence,
                ),
            )
            self._connection.executemany(
                """
                INSERT INTO quality_evidence
                    (id, finding_id, ordinal, role, artifact_version_id,
                     statement, observed, limit_value)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        f"evidence-{run_id}-{index}",
                        finding_id,
                        index,
                        item.role,
                        item.artifact_version_id,
                        item.statement,
                        item.observed,
                        item.limit,
                    )
                    for index, item in enumerate(evidence, start=1)
                ),
            )
            if plan is not None:
                self._append_plan_version(run_id)
        return self.get(run_id)

    def _insert_workflow_run(
        self,
        run_id: str,
        sources: VideoSources,
        video_version_id: str | None,
        plan: RepairPlan | None,
        clarification: ClarificationRequest | None,
        initial_status: WorkflowStatus,
    ) -> None:
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
                avatar_version_id, voice_version_id, active_video_version_id
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                NULL, ?, ?, ?, ?, ?
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
                video_version_id,
            ),
        )

    def _observed_artifact_id(
        self,
        kind: FailureKind,
        sources: VideoSources,
        video_version_id: str | None,
        explicit_id: str | None,
    ) -> str:
        expected_kind = {
            FailureKind.SCRIPT_QUALITY: ArtifactKind.SCRIPT,
            FailureKind.TTS_INPUT_COMPATIBILITY: ArtifactKind.TTS_INPUT,
            FailureKind.ENVIRONMENT_MISMATCH: ArtifactKind.SCRIPT,
            FailureKind.CAPTION_FORMAT: ArtifactKind.CAPTIONS,
            FailureKind.VISUAL_QUALITY: ArtifactKind.VIDEO,
        }[kind]
        inferred = {
            ArtifactKind.SCRIPT: sources.script_version_id,
            ArtifactKind.TTS_INPUT: sources.tts_input_version_id,
            ArtifactKind.VIDEO: video_version_id,
        }.get(expected_kind)
        observed_id = explicit_id or inferred
        if observed_id is None:
            raise ValueError("finding requires an exact observed artifact version")
        observed = self.get_artifact_version(observed_id)
        if observed.kind is not expected_kind:
            raise ValueError("observed artifact has the wrong kind for this finding")
        self._validate_observed_lineage(
            observed, expected_kind, inferred, sources, video_version_id
        )
        return observed_id

    def _validate_observed_lineage(
        self,
        observed: ArtifactVersion,
        expected_kind: ArtifactKind,
        inferred: str | None,
        sources: VideoSources,
        video_version_id: str | None,
    ) -> None:
        if expected_kind is ArtifactKind.VIDEO:
            self._validate_video_source(observed.id, sources)
            if video_version_id is not None and observed.id != video_version_id:
                raise ValueError(
                    "observed artifact must match the active video version"
                )
        elif expected_kind is ArtifactKind.CAPTIONS:
            if observed.source_versions != ((ArtifactKind.VIDEO, video_version_id),):
                raise ValueError("observed artifact must derive from the active video")
        elif observed.id != inferred:
            raise ValueError("observed artifact must match the selected source version")

    def _validate_evidence(
        self,
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

    def get_quality_finding(self, run_id: str) -> QualityFindingRecord:
        row = self._connection.execute(
            "SELECT * FROM quality_findings WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return QualityFindingRecord(
            id=row["id"],
            run_id=row["run_id"],
            artifact_version_id=row["artifact_version_id"],
            kind=FailureKind(row["kind"]),
            explanation=row["explanation"],
            confidence=row["confidence"],
            created_at=row["created_at"],
        )

    def get_quality_evidence(self, finding_id: str) -> tuple[EvidenceRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM quality_evidence WHERE finding_id = ? ORDER BY ordinal",
            (finding_id,),
        ).fetchall()
        return tuple(
            EvidenceRecord(
                id=row["id"],
                finding_id=row["finding_id"],
                role=EvidenceRole(row["role"]),
                artifact_version_id=row["artifact_version_id"],
                statement=row["statement"],
                observed=row["observed"],
                limit=row["limit_value"],
                created_at=row["created_at"],
            )
            for row in rows
        )

    def _validate_video_source(
        self, video_version_id: str, sources: VideoSources
    ) -> None:
        video = self.get_artifact_version(video_version_id)
        if video.kind is not ArtifactKind.VIDEO:
            raise ValueError("finding source must be a video version")
        if video.source_versions != sources.dependencies():
            raise ValueError("finding video must match selected source versions")

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

    def check_environment(self, sources: VideoSources) -> EnvironmentCheck:
        return check_script_avatar_compatibility(
            script_version_id=sources.script_version_id,
            authored_text=self.get_script_text(sources.script_version_id),
            scene=self.get_script_scene(sources.script_version_id),
            avatar_version_id=sources.avatar_version_id,
            avatar_environment=self.get_avatar_environment(sources.avatar_version_id),
        )

    def create_environment_run(
        self, *, run_id: str, sources: VideoSources
    ) -> WorkflowRun | None:
        """Open a human clarification only when the pre-render gate finds one."""

        checked = self.check_environment(sources)
        if checked.finding is None:
            return None
        assert checked.evidence is not None
        signal = checked.evidence
        evidence = (
            EvidenceInput(
                EvidenceRole.FACT,
                signal.script_version_id,
                f"Script contains scene phrase {signal.script_phrase!r}.",
                observed=signal.script_phrase,
                limit=signal.required_environment.value,
            ),
            EvidenceInput(
                EvidenceRole.FACT,
                signal.avatar_version_id,
                "Avatar declares an environment.",
                observed=signal.avatar_environment.value,
            ),
            EvidenceInput(
                EvidenceRole.INFERENCE,
                signal.script_version_id,
                checked.finding.explanation,
            ),
            EvidenceInput(
                EvidenceRole.UNCERTAINTY,
                signal.avatar_version_id,
                "Declared environment has not been checked against avatar pixels.",
            ),
        )
        return self.create(
            run_id=run_id, finding=checked.finding, sources=sources, evidence=evidence
        )

    def check_visual_quality(
        self, video_version_id: str, samples: tuple[MotionSample, ...]
    ) -> VisualSignalCheck:
        video = self.get_artifact_version(video_version_id)
        if video.kind is not ArtifactKind.VIDEO:
            raise ValueError("visual quality target must be a video version")
        return check_jerky_video(video_version_id, samples)

    def create_visual_quality_run(
        self,
        *,
        run_id: str,
        video_version_id: str,
        samples: tuple[MotionSample, ...],
    ) -> WorkflowRun | None:
        """Open an approval gate only for a grounded repeated-jerk signal."""

        checked = self.check_visual_quality(video_version_id, samples)
        if checked.finding is None:
            return None
        video = self.get_artifact_version(video_version_id)
        source_ids = dict(video.source_versions)
        sources = VideoSources(
            script_version_id=source_ids[ArtifactKind.SCRIPT],
            tts_input_version_id=source_ids[ArtifactKind.TTS_INPUT],
            avatar_version_id=source_ids[ArtifactKind.AVATAR],
            voice_version_id=source_ids[ArtifactKind.VOICE],
        )
        return self.create(
            run_id=run_id,
            finding=checked.finding,
            sources=sources,
            video_version_id=video_version_id,
            evidence=tuple(
                EvidenceInput(
                    EvidenceRole.FACT,
                    item.video_version_id,
                    f"Motion jump from frame {item.from_frame} to {item.to_frame}.",
                    observed=f"{item.jump_px_per_frame:g} px/frame",
                    limit=f"{item.threshold_px:g} px/frame",
                )
                for item in checked.evidence
            )
            + (
                EvidenceInput(
                    EvidenceRole.INFERENCE,
                    video_version_id,
                    checked.finding.explanation,
                ),
                EvidenceInput(
                    EvidenceRole.UNCERTAINTY,
                    video_version_id,
                    checked.demo_notice,
                ),
            ),
        )

    def create_caption_quality_run(
        self,
        *,
        run_id: str,
        caption_version_id: str,
    ) -> WorkflowRun | None:
        """Persist a caption finding against one exact observed caption version."""

        caption = self.get_artifact_version(caption_version_id)
        if caption.kind is not ArtifactKind.CAPTIONS:
            raise ValueError("caption quality target must be a caption version")
        checked = validate_captions(self.get_caption_cues(caption_version_id))
        if checked.valid:
            return None
        if len(caption.source_versions) != 1:
            raise ValueError("caption must derive from one video version")
        video_version_id = caption.source_versions[0][1]
        video = self.get_artifact_version(video_version_id)
        source_ids = dict(video.source_versions)
        sources = VideoSources(
            script_version_id=source_ids[ArtifactKind.SCRIPT],
            tts_input_version_id=source_ids[ArtifactKind.TTS_INPUT],
            avatar_version_id=source_ids[ArtifactKind.AVATAR],
            voice_version_id=source_ids[ArtifactKind.VOICE],
        )
        return self.create(
            run_id=run_id,
            finding=QualityFinding(
                FailureKind.CAPTION_FORMAT,
                f"{caption_version_id} violates {len(checked.evidence)} caption rule(s).",
                1.0,
            ),
            sources=sources,
            video_version_id=video_version_id,
            observed_artifact_version_id=caption_version_id,
            evidence=tuple(
                EvidenceInput(
                    EvidenceRole.FACT,
                    caption_version_id,
                    f"Caption rule {item.rule} failed at cue {item.cue_index}.",
                    observed=item.observed,
                    limit=item.limit,
                )
                for item in checked.evidence
            )
            + (
                EvidenceInput(
                    EvidenceRole.INFERENCE,
                    caption_version_id,
                    "The measured rule violations require local caption repair.",
                ),
                EvidenceInput(
                    EvidenceRole.UNCERTAINTY,
                    caption_version_id,
                    "Caption meaning and visual fit have not been reviewed.",
                ),
            ),
        )

    def select_repair(self, run_id: str, action: RepairAction) -> WorkflowRun:
        """Record a branch choice while awaiting its replacement artifact."""

        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
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
            if result.rowcount != 1:
                raise RuntimeError("workflow state changed during clarification")
            self._append_plan_version(run_id)
            return self.get(run_id)

    def approve(self, run_id: str, *, plan_version_id: str | None) -> WorkflowRun:
        """Approve the exact snapshot reviewed by a human, under a writer lock."""

        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.get(run_id)
            if current.status is not WorkflowStatus.AWAITING_APPROVAL:
                raise ValueError("workflow is not awaiting approval")
            if plan_version_id is None or current.plan_version_id != plan_version_id:
                raise ValueError("reviewed plan version is no longer current")
            self._validate_tts_binding(current.sources)
            self._validate_environment_before_approval(current)
            self._connection.execute(
                "INSERT INTO plan_approvals (plan_version_id) VALUES (?)",
                (plan_version_id,),
            )
            if not has_current_approval(self.get(run_id)):
                raise ValueError(
                    "plan-version approval does not match current artifacts"
                )
            self._connection.execute(
                """UPDATE workflow_runs SET status = ?,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?""",
                (WorkflowStatus.READY, run_id),
            )
        return self.get(run_id)

    def revise_plan(
        self, run_id: str, *, plan: RepairPlan, expected_plan_version_id: str | None
    ) -> WorkflowRun:
        """Append a policy-constrained edit and invalidate the current approval."""

        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.get(run_id)
            if current.status not in {
                WorkflowStatus.NEEDS_REPAIR_INPUT,
                WorkflowStatus.AWAITING_APPROVAL,
                WorkflowStatus.READY,
            }:
                raise ValueError("workflow plan cannot be edited in this state")
            if (
                expected_plan_version_id is None
                or current.plan_version_id != expected_plan_version_id
            ):
                raise ValueError("reviewed plan version is no longer current")
            record = self.get_quality_finding(run_id)
            validate_plan_revision(
                QualityFinding(record.kind, record.explanation, record.confidence), plan
            )
            status = self._revised_plan_status(current, plan)
            self._connection.execute(
                """UPDATE workflow_runs SET action = ?, invalidates = ?,
                   requires_repair_input = ?, rationale = ?, status = ?,
                   idempotency_key = ?,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?""",
                (
                    plan.action,
                    _encode_invalidates(plan.invalidates),
                    plan.requires_repair_input,
                    plan.rationale,
                    status,
                    None
                    if status is WorkflowStatus.NEEDS_REPAIR_INPUT
                    else current.idempotency_key,
                    run_id,
                ),
            )
            self._append_plan_version(run_id)
        return self.get(run_id)

    def _revised_plan_status(
        self, current: WorkflowRun, plan: RepairPlan
    ) -> WorkflowStatus:
        if current.plan is not None and current.plan.action is plan.action:
            if current.status is WorkflowStatus.NEEDS_REPAIR_INPUT:
                return WorkflowStatus.NEEDS_REPAIR_INPUT
        elif plan.requires_repair_input:
            return WorkflowStatus.NEEDS_REPAIR_INPUT
        return WorkflowStatus.AWAITING_APPROVAL

    def _upgrade_unversioned_plans(self) -> None:
        """Snapshot legacy plans; never infer human approval from a READY status."""

        rows = self._connection.execute(
            """SELECT id FROM workflow_runs WHERE action IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM repair_plan_versions WHERE run_id = workflow_runs.id
            )"""
        ).fetchall()
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            for row in rows:
                self._append_plan_version(row["id"])
                self._connection.execute(
                    "UPDATE workflow_runs SET status = ? WHERE id = ? AND status = ?",
                    (WorkflowStatus.AWAITING_APPROVAL, row["id"], WorkflowStatus.READY),
                )

    def _append_plan_version(self, run_id: str) -> None:
        current = self.get(run_id)
        if current.plan is None:
            raise ValueError("workflow has no repair plan to version")
        previous = current.plan_version
        revision = previous.revision + 1 if previous else 1
        version_id = f"plan-{run_id}-{revision}"
        choices = dict(previous.replacement_choices) if previous else {}
        if previous is not None:
            old_sources = dict(previous.sources.dependencies())
            choices.update(
                (k, v) for k, v in current.sources.dependencies() if old_sources[k] != v
            )
        key = current.idempotency_key
        if revision > 1 and key is not None:
            key = f"workflow-run:{run_id}:plan:{version_id}"
            self._connection.execute(
                "UPDATE workflow_runs SET idempotency_key = ? WHERE id = ?",
                (key, run_id),
            )
        version = RepairPlanVersion(
            version_id,
            run_id,
            revision,
            current.plan,
            current.sources,
            current.active_video_version_id,
            current.active_caption_version_id,
            self.get_quality_finding(run_id).artifact_version_id,
            tuple(sorted(choices.items())),
            key,
            "",
        )
        self._connection.execute(
            "INSERT INTO repair_plan_versions (id, run_id, revision, snapshot) VALUES (?, ?, ?, ?)",
            (version_id, run_id, revision, encode_plan_version(version)),
        )

    def get_plan_versions(self, run_id: str) -> tuple[RepairPlanVersion, ...]:
        rows = self._connection.execute(
            "SELECT snapshot, created_at FROM repair_plan_versions WHERE run_id = ? ORDER BY revision",
            (run_id,),
        ).fetchall()
        return tuple(
            decode_plan_version(row["snapshot"], row["created_at"]) for row in rows
        )

    def get_plan_approval(self, plan_version_id: str) -> PlanApproval | None:
        row = self._connection.execute(
            "SELECT * FROM plan_approvals WHERE plan_version_id = ?",
            (plan_version_id,),
        ).fetchone()
        return PlanApproval(row["plan_version_id"], row["created_at"]) if row else None

    def _validate_environment_before_approval(self, run: WorkflowRun) -> None:
        if run.plan is None or run.plan.action is RepairAction.REPAIR_CAPTIONS:
            return
        if self.check_environment(run.sources).finding is not None:
            raise ValueError("environment mismatch must be resolved before approval")

    def bind_replacement(
        self,
        run_id: str,
        version_id: str,
        *,
        expected_plan_version_id: str | None = None,
    ) -> WorkflowRun:
        """Bind the exact new input required by a repair before approval."""

        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.get(run_id)
            self._require_optional_version(current, expected_plan_version_id)
            if (
                current.status
                not in {
                    WorkflowStatus.NEEDS_REPAIR_INPUT,
                    WorkflowStatus.AWAITING_APPROVAL,
                    WorkflowStatus.READY,
                }
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
            key = (
                f"workflow-run:{run_id}:{current.plan.action}:replacement:{version_id}"
            )
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
                    current.status,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("workflow state changed during replacement binding")
            self._append_plan_version(run_id)
            return self.get(run_id)

    def bind_tts_input(
        self,
        run_id: str,
        version_id: str,
        *,
        expected_plan_version_id: str | None = None,
    ) -> WorkflowRun:
        """Pair a revised script with its validated spoken text before approval."""

        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.get(run_id)
            self._require_optional_version(current, expected_plan_version_id)
            if (
                current.status
                not in {WorkflowStatus.AWAITING_APPROVAL, WorkflowStatus.READY}
                or current.plan is None
                or current.plan.action is not RepairAction.REVISE_SCRIPT
            ):
                raise ValueError("workflow is not awaiting revised script TTS input")
            tts_input = self.get_tts_input_version(version_id)
            if tts_input.script_version_id != current.sources.script_version_id:
                raise ValueError(
                    "TTS input must derive from the selected script version"
                )
            if version_id == current.sources.tts_input_version_id:
                raise ValueError("replacement TTS input must be a new version")
            key = (
                f"workflow-run:{run_id}:{current.plan.action}:"
                f"replacement:{current.sources.script_version_id}:tts:{version_id}"
            )
            updated = self._connection.execute(
                """
                UPDATE workflow_runs
                SET tts_input_version_id = ?, idempotency_key = ?, status = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ? AND status = ? AND script_version_id = ?
                """,
                (
                    version_id,
                    key,
                    WorkflowStatus.AWAITING_APPROVAL,
                    run_id,
                    current.status,
                    current.sources.script_version_id,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("workflow state changed during TTS input binding")
            self._append_plan_version(run_id)
            return self.get(run_id)

    def reserve_submission(
        self,
        *,
        run_id: str,
        expected_plan_version_id: str | None,
        lease: SubmissionLease | None = None,
    ) -> WorkflowRun:
        """Lock the approved revision before an external submission can begin.

        A repeated reservation resumes the same durable attempt and key.
        Provider errors leave it reserved because acceptance may be unknown.
        """

        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            require_submission_lease(
                self._connection, lease, self._clock(), run_id, expected_plan_version_id
            )
            current = self.get(run_id)
            self._require_current_plan_version(current, expected_plan_version_id)
            if current.status is WorkflowStatus.SUBMITTED:
                return current
            if current.status not in {WorkflowStatus.READY, WorkflowStatus.SUBMITTING}:
                raise ValueError("workflow is not ready for provider submission")
            self._require_video_submission_plan(current)
            if not has_current_approval(current):
                raise ValueError("workflow has no current plan-version approval")
            self._connection.execute(
                """
                UPDATE workflow_runs
                SET status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (WorkflowStatus.SUBMITTING, run_id),
            )
            return self.get(run_id)

    def record_submission(
        self,
        *,
        run_id: str,
        external_job_id: str,
        expected_plan_version_id: str | None,
        lease: SubmissionLease | None = None,
    ) -> WorkflowRun:
        """Record an accepted provider job without permitting ID replacement."""

        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            require_submission_lease(
                self._connection, lease, self._clock(), run_id, expected_plan_version_id
            )
            validate_external_id(external_job_id, "provider job")
            current = self.get(run_id)
            self._require_current_plan_version(current, expected_plan_version_id)
            if current.status is WorkflowStatus.SUBMITTED:
                if current.external_job_id != external_job_id:
                    raise ValueError("workflow already references another provider job")
                return current
            plan = self._require_video_submission_plan(current)
            if current.status is not WorkflowStatus.SUBMITTING:
                raise ValueError("workflow has no reserved provider submission")
            if not has_current_approval(current):
                raise ValueError("workflow has no current plan-version approval")
            self._connection.execute(
                """
                INSERT INTO provider_jobs (
                    external_job_id, run_id, idempotency_key, action, plan_version_id,
                    script_version_id, tts_input_version_id,
                    avatar_version_id, voice_version_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    external_job_id,
                    run_id,
                    current.idempotency_key,
                    plan.action,
                    current.plan_version_id,
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
                    WorkflowStatus.SUBMITTING,
                ),
            )
            if result.rowcount != 1:
                raise RuntimeError("workflow state changed during provider submission")
            return self.get(run_id)

    def _require_current_plan_version(
        self, run: WorkflowRun, expected: str | None
    ) -> None:
        if expected is None or run.plan_version_id != expected:
            raise ValueError("submitted plan version is no longer current")

    def _require_optional_version(self, run: WorkflowRun, expected: str | None) -> None:
        if expected is not None:
            self._require_current_plan_version(run, expected)

    def _require_video_submission_plan(self, run: WorkflowRun) -> RepairPlan:
        if run.plan is None or run.idempotency_key is None:
            raise ValueError("workflow has no executable repair plan")
        if run.plan.action is RepairAction.REPAIR_CAPTIONS:
            raise ValueError("caption repair cannot submit a video provider job")
        return run.plan

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

    def request_retry(
        self, run_id: str, *, expected_plan_version_id: str | None = None
    ) -> WorkflowRun:
        """Supersede an in-flight job and require approval for a new attempt."""

        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.get(run_id)
            self._require_optional_version(current, expected_plan_version_id)
            if (
                current.status
                not in {WorkflowStatus.SUBMITTED, WorkflowStatus.SUCCEEDED}
                or current.plan is None
                or current.external_job_id is None
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
            self._append_plan_version(run_id)
        return self.get(run_id)

    def list_run_ids(self) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT id FROM workflow_runs ORDER BY created_at DESC, id LIMIT 100"
        ).fetchall()
        return tuple(row["id"] for row in rows)

    def list_provider_jobs(self, run_id: str) -> tuple[ProviderJob, ...]:
        rows = self._connection.execute(
            "SELECT external_job_id FROM provider_jobs WHERE run_id = ? ORDER BY created_at, external_job_id",
            (run_id,),
        ).fetchall()
        return tuple(self.get_provider_job(row[0]) for row in rows)

    def list_provider_events(self, run_id: str) -> tuple[ProviderEvent, ...]:
        rows = self._connection.execute(
            """SELECT e.external_event_id FROM provider_events e JOIN provider_jobs j
               ON j.external_job_id = e.external_job_id WHERE j.run_id = ?
               ORDER BY e.created_at, e.external_event_id""",
            (run_id,),
        ).fetchall()
        return tuple(self.get_provider_event(row[0]) for row in rows)

    def generated_videos(self, run_id: str) -> tuple[ArtifactVersion, ...]:
        rows = self._connection.execute(
            """SELECT v.id FROM artifact_versions v JOIN provider_jobs j
               ON j.external_job_id = v.external_job_id WHERE j.run_id = ?
               AND v.kind = 'video' ORDER BY v.created_at, v.id""",
            (run_id,),
        ).fetchall()
        return tuple(self.get_artifact_version(row[0]) for row in rows)

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
            plan_version_id=row["plan_version_id"],
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
        versions = self.get_plan_versions(run_id)
        version = versions[-1] if versions else None
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
            active_caption_version_id=row["active_caption_version_id"],
            plan_version=version,
            approval=self.get_plan_approval(version.id) if version else None,
        )


def _encode_invalidates(invalidates: frozenset[ArtifactKind]) -> str:
    return ",".join(sorted(artifact.value for artifact in invalidates))


def _encode_cues(cues: tuple[CaptionCue, ...]) -> str:
    return json.dumps(
        [
            {"start_ms": cue.start_ms, "end_ms": cue.end_ms, "text": cue.text}
            for cue in cues
        ]
    )


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
