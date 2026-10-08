"""Durable workflow transitions: plans, approval, submission, and completion.

Every transition runs in one SQLite writer transaction. A run points at its
current immutable plan version; any plan change appends a version and moves the
pointer in the same transaction, which withdraws any earlier approval.
"""

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, replace

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
from media_qc_agent.workflow.artifacts import ArtifactStore
from media_qc_agent.workflow.findings import (
    observed_artifact_id,
    validate_evidence,
    validate_video_source,
)
from media_qc_agent.workflow.gate_runs import check_environment
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
    require_approved_video_plan,
    require_video_submission,
)
from media_qc_agent.workflow.plan_versions import (
    decode_plan_version,
    encode_plan_version,
    validate_plan_revision,
)
from media_qc_agent.workflow.schema import initialize_schema
from media_qc_agent.workflow.telemetry import (
    EventKind,
    ExecutionObserver,
    error_category,
    event_for_completion,
    event_for_run,
    reference,
)
from media_qc_agent.workflow.timing import JobTiming, job_timing
from media_qc_agent.workflow.tracing import completion_span
from media_qc_agent.workflow.worker_models import SubmissionLease
from media_qc_agent.workflow.worker_queue import require_submission_lease

_EDITABLE = frozenset(
    {
        WorkflowStatus.NEEDS_REPAIR_INPUT,
        WorkflowStatus.AWAITING_APPROVAL,
        WorkflowStatus.READY,
    }
)
_REPLACEABLE_SOURCE = {
    RepairAction.REVISE_SCRIPT: ArtifactKind.SCRIPT,
    RepairAction.REPAIR_TTS_INPUT: ArtifactKind.TTS_INPUT,
    RepairAction.CHANGE_AVATAR: ArtifactKind.AVATAR,
}
_SOURCE_FIELD = {
    ArtifactKind.SCRIPT: "script_version_id",
    ArtifactKind.TTS_INPUT: "tts_input_version_id",
    ArtifactKind.AVATAR: "avatar_version_id",
}
STALE_PLAN = (
    "The reviewed plan version is no longer current. "
    "Refresh and review the current version."
)
_TOUCH = "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


class WorkflowRepository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock: Callable[[], float] = time.time,
        observer: ExecutionObserver | None = None,
    ) -> None:
        self._connection = connection
        self._clock = clock
        self._observer = observer or ExecutionObserver()
        self._connection.row_factory = sqlite3.Row
        self.artifacts = ArtifactStore(connection)

    def initialize(self) -> None:
        initialize_schema(self._connection)

    # Creating a run

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
        """Persist a finding with its evidence and the policy's first decision."""

        validate_run_id(run_id)
        self.artifacts.validate_sources(sources)
        if finding.kind is FailureKind.CAPTION_FORMAT and video_version_id is None:
            raise ValueError("caption finding requires an existing video version")
        if video_version_id is not None:
            validate_video_source(self.artifacts, video_version_id, sources)
        observed_id = observed_artifact_id(
            self.artifacts,
            finding.kind,
            sources,
            video_version_id,
            observed_artifact_version_id,
        )
        evidence = evidence or (
            EvidenceInput(EvidenceRole.INFERENCE, observed_id, finding.explanation),
            EvidenceInput(
                EvidenceRole.UNCERTAINTY,
                observed_id,
                "No independent validator evidence was supplied for this finding.",
            ),
        )
        validate_evidence(evidence, sources, observed_id, video_version_id)
        decision = plan_repair(finding)
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                """INSERT INTO workflow_runs (
                       id, status, clarification, observed_script_version_id,
                       observed_tts_input_version_id, observed_avatar_version_id,
                       observed_voice_version_id, active_video_version_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    WorkflowStatus.NEEDS_INPUT,
                    _encode_clarification(decision)
                    if isinstance(decision, ClarificationRequest)
                    else None,
                    *(version for _, version in sources.dependencies()),
                    video_version_id,
                ),
            )
            self._insert_finding(run_id, observed_id, finding, evidence)
            if isinstance(decision, RepairPlan):
                self._change_plan(
                    run_id,
                    decision,
                    sources,
                    _first_status(decision),
                    expected_status=WorkflowStatus.NEEDS_INPUT,
                )
        return self.get(run_id)

    def _insert_finding(
        self,
        run_id: str,
        observed_id: str,
        finding: QualityFinding,
        evidence: tuple[EvidenceInput, ...],
    ) -> None:
        finding_id = f"finding-{run_id}"
        self._connection.execute(
            """INSERT INTO quality_findings
                   (id, run_id, artifact_version_id, kind, explanation, confidence)
               VALUES (?, ?, ?, ?, ?, ?)""",
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
            """INSERT INTO quality_evidence
                   (id, finding_id, ordinal, role, artifact_version_id,
                    statement, observed, limit_value)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
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

    # Changing the plan

    def select_repair(
        self,
        run_id: str,
        action: RepairAction,
        *,
        expected_plan_version_id: str | None,
    ) -> WorkflowRun:
        """Record a human's creative choice; the repair then awaits its input."""

        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.get(run_id)
            _require_current_version(current, expected_plan_version_id)
            if (
                current.status is not WorkflowStatus.NEEDS_INPUT
                or current.clarification is None
            ):
                raise ValueError("workflow is not awaiting clarification")
            plan = select_repair(current.clarification, action)
            self._change_plan(
                run_id,
                plan,
                current.sources,
                WorkflowStatus.NEEDS_REPAIR_INPUT,
                expected_status=current.status,
            )
        return self.get(run_id)

    def revise_plan(
        self, run_id: str, *, plan: RepairPlan, expected_plan_version_id: str | None
    ) -> WorkflowRun:
        """Append a policy-constrained edit; any earlier approval no longer applies."""

        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.get(run_id)
            if current.status not in _EDITABLE:
                raise ValueError("workflow plan cannot be edited in this state")
            _require_current_version(current, expected_plan_version_id)
            record = self.get_quality_finding(run_id)
            validate_plan_revision(
                QualityFinding(record.kind, record.explanation, record.confidence), plan
            )
            self._change_plan(
                run_id,
                plan,
                current.sources,
                _revised_status(current, plan),
                expected_status=current.status,
            )
        return self.get(run_id)

    def bind_replacement(
        self,
        run_id: str,
        version_id: str,
        *,
        expected_plan_version_id: str | None,
    ) -> WorkflowRun:
        """Bind the exact new input a repair requires before approval."""

        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.get(run_id)
            _require_current_version(current, expected_plan_version_id)
            if current.status not in _EDITABLE or current.plan is None:
                raise ValueError("workflow is not awaiting repair input")
            kind = _REPLACEABLE_SOURCE.get(current.plan.action)
            if kind is None:
                raise ValueError("repair action has no replacement input")
            if self.artifacts.get(version_id).kind is not kind:
                raise ValueError(f"replacement must be a {kind.value} version")
            if version_id in {value for _, value in current.sources.dependencies()}:
                raise ValueError("replacement must be a new source version")
            sources = replace(current.sources, **{_SOURCE_FIELD[kind]: version_id})
            self._change_plan(
                run_id,
                current.plan,
                sources,
                WorkflowStatus.AWAITING_APPROVAL,
                expected_status=current.status,
            )
        return self.get(run_id)

    def bind_tts_input(
        self,
        run_id: str,
        version_id: str,
        *,
        expected_plan_version_id: str | None,
    ) -> WorkflowRun:
        """Pair a revised script with spoken text derived from it."""

        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.get(run_id)
            _require_current_version(current, expected_plan_version_id)
            if (
                current.status
                not in {WorkflowStatus.AWAITING_APPROVAL, WorkflowStatus.READY}
                or current.plan is None
                or current.plan.action is not RepairAction.REVISE_SCRIPT
            ):
                raise ValueError("workflow is not awaiting revised script TTS input")
            tts_input = self.artifacts.tts_input(version_id)
            if tts_input.script_version_id != current.sources.script_version_id:
                raise ValueError(
                    "TTS input must derive from the selected script version"
                )
            if version_id == current.sources.tts_input_version_id:
                raise ValueError("replacement TTS input must be a new version")
            self._change_plan(
                run_id,
                current.plan,
                replace(current.sources, tts_input_version_id=version_id),
                WorkflowStatus.AWAITING_APPROVAL,
                expected_status=current.status,
            )
        return self.get(run_id)

    def request_retry(
        self, run_id: str, *, expected_plan_version_id: str | None
    ) -> WorkflowRun:
        """Supersede the submitted job; the new attempt needs its own approval."""

        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.get(run_id)
            _require_current_version(current, expected_plan_version_id)
            if (
                current.status
                not in {WorkflowStatus.SUBMITTED, WorkflowStatus.SUCCEEDED}
                or current.plan is None
                or current.external_job_id is None
            ):
                raise ValueError("workflow has no submitted job to retry")
            self._connection.execute(
                "UPDATE workflow_runs SET external_job_id = NULL WHERE id = ?",
                (run_id,),
            )
            self._change_plan(
                run_id,
                current.plan,
                current.sources,
                WorkflowStatus.AWAITING_APPROVAL,
                expected_status=current.status,
            )
            requested = self.get(run_id)
        self._observer.emit(
            event_for_run(
                EventKind.NEW_ATTEMPT,
                requested,
                job_id=current.external_job_id,
                state_before=current.status,
                state_after=requested.status,
            )
        )
        return requested

    def _change_plan(
        self,
        run_id: str,
        plan: RepairPlan,
        sources: VideoSources,
        status: WorkflowStatus,
        *,
        expected_status: WorkflowStatus,
    ) -> None:
        """Append the next plan version and make it current, within a transaction.

        A version waiting for repair input has no recovery key; any other gets a
        key unique to that version, so each approved attempt is distinct.
        """

        current = self.get(run_id)
        previous = current.plan_version
        revision = previous.revision + 1 if previous else 1
        version_id = f"plan-{run_id}-{revision}"
        choices = dict(previous.replacement_choices) if previous else {}
        if previous is not None:
            before = dict(previous.sources.dependencies())
            choices.update(
                (kind, value)
                for kind, value in sources.dependencies()
                if before[kind] != value
            )
        version = RepairPlanVersion(
            id=version_id,
            run_id=run_id,
            revision=revision,
            plan=plan,
            sources=sources,
            target_video_version_id=current.active_video_version_id,
            target_caption_version_id=current.active_caption_version_id,
            observed_artifact_version_id=self.get_quality_finding(
                run_id
            ).artifact_version_id,
            replacement_choices=tuple(sorted(choices.items())),
            idempotency_key=None
            if status is WorkflowStatus.NEEDS_REPAIR_INPUT
            else f"workflow-run:{run_id}:plan:{version_id}",
            created_at="",
        )
        self._connection.execute(
            """INSERT INTO repair_plan_versions (id, run_id, revision, snapshot)
               VALUES (?, ?, ?, ?)""",
            (version_id, run_id, revision, encode_plan_version(version)),
        )
        updated = self._connection.execute(
            f"""UPDATE workflow_runs
                SET plan_version_id = ?, status = ?, clarification = NULL, {_TOUCH}
                WHERE id = ? AND status = ?""",
            (version_id, status, run_id, expected_status),
        )
        if updated.rowcount != 1:
            raise RuntimeError("workflow state changed during plan change")

    # Approval and execution

    def approve(self, run_id: str, *, plan_version_id: str | None) -> WorkflowRun:
        """Approve the exact plan version a human reviewed."""

        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            current = self.get(run_id)
            if current.status is not WorkflowStatus.AWAITING_APPROVAL:
                raise ValueError("workflow is not awaiting approval")
            if plan_version_id is None:
                raise ValueError(STALE_PLAN)
            _require_current_version(current, plan_version_id)
            self.artifacts.validate_tts_binding(current.sources)
            self._require_environment_resolved(current)
            self._connection.execute(
                "INSERT INTO plan_approvals (plan_version_id) VALUES (?)",
                (plan_version_id,),
            )
            self._set_status(run_id, WorkflowStatus.READY)
            approved = self.get(run_id)
        self._observer.emit(
            event_for_run(
                EventKind.APPROVED,
                approved,
                state_before=current.status,
                state_after=approved.status,
            )
        )
        return approved

    def _require_environment_resolved(self, run: WorkflowRun) -> None:
        if run.plan is None or run.plan.action is RepairAction.REPAIR_CAPTIONS:
            return
        if check_environment(self.artifacts, run.sources).finding is not None:
            raise ValueError("environment mismatch must be resolved before approval")

    def reserve_submission(
        self,
        *,
        run_id: str,
        expected_plan_version_id: str | None,
        lease: SubmissionLease | None = None,
    ) -> WorkflowRun:
        """Lock the approved version before an external submission can begin.

        A repeated reservation resumes the same attempt and key. Provider errors
        leave it reserved because acceptance may be unknown.
        """

        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            require_submission_lease(
                self._connection, lease, self._clock(), run_id, expected_plan_version_id
            )
            current = self.get(run_id)
            _require_current_version(current, expected_plan_version_id)
            if current.status is WorkflowStatus.SUBMITTED:
                return current
            require_video_submission(current)
            self._set_status(run_id, WorkflowStatus.SUBMITTING)
            reserved = self.get(run_id)
        self._observer.emit(
            event_for_run(
                EventKind.RESERVED,
                reserved,
                state_before=current.status,
                state_after=reserved.status,
            )
        )
        return reserved

    def record_submission(
        self,
        *,
        run_id: str,
        external_job_id: str,
        expected_plan_version_id: str | None,
        lease: SubmissionLease | None = None,
        traceparent: str | None = None,
    ) -> WorkflowRun:
        """Record an accepted provider job; never replace a recorded one."""

        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            require_submission_lease(
                self._connection, lease, self._clock(), run_id, expected_plan_version_id
            )
            validate_external_id(external_job_id, "provider job")
            current = self.get(run_id)
            _require_current_version(current, expected_plan_version_id)
            if current.status is WorkflowStatus.SUBMITTED:
                if current.external_job_id != external_job_id:
                    raise ValueError("workflow already references another provider job")
                return current
            require_approved_video_plan(current)
            if current.status is not WorkflowStatus.SUBMITTING:
                raise ValueError("workflow has no reserved provider submission")
            self._connection.execute(
                """INSERT INTO provider_jobs (
                       external_job_id, run_id, plan_version_id, idempotency_key,
                       traceparent
                   ) VALUES (?, ?, ?, ?, ?)""",
                (
                    external_job_id,
                    run_id,
                    current.plan_version_id,
                    current.idempotency_key,
                    traceparent,
                ),
            )
            updated = self._connection.execute(
                f"""UPDATE workflow_runs SET status = ?, external_job_id = ?, {_TOUCH}
                    WHERE id = ? AND status = ?""",
                (
                    WorkflowStatus.SUBMITTED,
                    external_job_id,
                    run_id,
                    WorkflowStatus.SUBMITTING,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("workflow state changed during provider submission")
        return self.get(run_id)

    def record_completion(
        self, *, external_job_id: str, external_event_id: str
    ) -> WorkflowRun:
        """Audit a completion; only the currently submitted job advances the run."""

        started = self._observer.clock()
        row = self._connection.execute(
            "SELECT traceparent FROM provider_jobs WHERE external_job_id = ?",
            (external_job_id,),
        ).fetchone()
        with completion_span(
            self._observer.tracer,
            external_job_id,
            row[0] if row else None,
            self._observer.trace_failed,
        ) as span:
            try:
                job, disposition = self._commit_completion(
                    external_job_id=external_job_id, external_event_id=external_event_id
                )
            except Exception as error:
                span.error(error_category(error))
                raise
            span.outcome(disposition.value)
        event = event_for_completion(job, external_event_id, disposition)
        self._observer.emit(
            replace(event, duration_seconds=self._observer.clock() - started)
        )
        if disposition is ProviderEventDisposition.APPLIED:
            self._observer.emit(
                replace(
                    event,
                    kind=EventKind.ARTIFACT_CREATED,
                    artifact_ref=reference(video_version_id(job.external_job_id)),
                    artifact_kind=ArtifactKind.VIDEO.value,
                    state_before=WorkflowStatus.SUBMITTED,
                    state_after=WorkflowStatus.SUCCEEDED,
                )
            )
        return self.get(job.run_id)

    def _commit_completion(
        self, *, external_job_id: str, external_event_id: str
    ) -> tuple[ProviderJob, ProviderEventDisposition]:
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
                """INSERT OR IGNORE INTO provider_events (
                       external_event_id, external_job_id, event_type, result_status,
                       disposition, reason
                   ) VALUES (?, ?, 'completed', ?, ?, ?)""",
                (
                    external_event_id,
                    external_job_id,
                    WorkflowStatus.SUCCEEDED,
                    disposition,
                    reason,
                ),
            )
            if inserted.rowcount == 0:
                if self.get_provider_event(external_event_id).external_job_id != (
                    external_job_id
                ):
                    raise ValueError("event identifier belongs to another provider job")
                return job, ProviderEventDisposition.REDUNDANT
            if disposition is ProviderEventDisposition.APPLIED:
                self._promote_generated_video(job)
        return job, disposition

    def _promote_generated_video(self, job: ProviderJob) -> None:
        generated_id = video_version_id(job.external_job_id)
        self.artifacts.insert_video(generated_id, job.sources, job.external_job_id)
        updated = self._connection.execute(
            f"""UPDATE workflow_runs SET status = ?, active_video_version_id = ?, {_TOUCH}
                WHERE id = ? AND status = ? AND external_job_id = ?""",
            (
                WorkflowStatus.SUCCEEDED,
                generated_id,
                job.run_id,
                WorkflowStatus.SUBMITTED,
                job.external_job_id,
            ),
        )
        if updated.rowcount != 1:
            raise RuntimeError("workflow state changed during provider completion")

    def record_caption_repair(
        self,
        *,
        run_id: str,
        version_id: str,
        cues: tuple[CaptionCue, ...],
        expected_plan_version_id: str | None,
    ) -> ArtifactVersion:
        """Promote validated captions locally; the accepted video is kept."""

        validate_artifact_version_id(version_id, ArtifactKind.CAPTIONS)
        checked = validate_captions(cues)
        if not checked.valid:
            raise UnsafeCaptions(checked.evidence)
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            run = self.get(run_id)
            _require_current_version(run, expected_plan_version_id)
            if (
                run.status is not WorkflowStatus.READY
                or run.plan is None
                or run.plan.action is not RepairAction.REPAIR_CAPTIONS
                or run.active_video_version_id is None
            ):
                raise ValueError("workflow is not ready for caption repair")
            if not has_current_approval(run):
                raise ValueError("workflow has no current plan-version approval")
            self.artifacts.insert_captions(
                version_id, run.active_video_version_id, cues
            )
            updated = self._connection.execute(
                f"""UPDATE workflow_runs
                    SET status = ?, active_caption_version_id = ?, {_TOUCH}
                    WHERE id = ? AND status = ? AND active_video_version_id = ?""",
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
        self._observer.emit(
            replace(
                event_for_run(
                    EventKind.ARTIFACT_CREATED,
                    run,
                    state_before=WorkflowStatus.READY,
                    state_after=WorkflowStatus.SUCCEEDED,
                ),
                artifact_ref=reference(version_id),
                artifact_kind=ArtifactKind.CAPTIONS.value,
            )
        )
        return self.artifacts.get(version_id)

    def _set_status(self, run_id: str, status: WorkflowStatus) -> None:
        self._connection.execute(
            f"UPDATE workflow_runs SET status = ?, {_TOUCH} WHERE id = ?",
            (status, run_id),
        )

    # Reads

    def get(self, run_id: str) -> WorkflowRun:
        row = self._connection.execute(
            "SELECT * FROM workflow_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        version = (
            self.get_plan_version(row["plan_version_id"])
            if row["plan_version_id"]
            else None
        )
        observed_sources = VideoSources(
            script_version_id=row["observed_script_version_id"],
            tts_input_version_id=row["observed_tts_input_version_id"],
            avatar_version_id=row["observed_avatar_version_id"],
            voice_version_id=row["observed_voice_version_id"],
        )
        return WorkflowRun(
            id=row["id"],
            status=WorkflowStatus(row["status"]),
            plan=version.plan if version else None,
            clarification=_decode_clarification(row["clarification"]),
            idempotency_key=version.idempotency_key if version else None,
            external_job_id=row["external_job_id"],
            sources=version.sources if version else observed_sources,
            active_video_version_id=row["active_video_version_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            active_caption_version_id=row["active_caption_version_id"],
            plan_version=version,
            approval=self.get_plan_approval(version.id) if version else None,
        )

    def list_run_ids(self) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT id FROM workflow_runs ORDER BY created_at DESC, id LIMIT 100"
        ).fetchall()
        return tuple(row["id"] for row in rows)

    def get_plan_versions(self, run_id: str) -> tuple[RepairPlanVersion, ...]:
        rows = self._connection.execute(
            """SELECT snapshot, created_at FROM repair_plan_versions
               WHERE run_id = ? ORDER BY revision""",
            (run_id,),
        ).fetchall()
        return tuple(
            decode_plan_version(row["snapshot"], row["created_at"]) for row in rows
        )

    def get_plan_version(self, version_id: str) -> RepairPlanVersion:
        row = self._connection.execute(
            "SELECT snapshot, created_at FROM repair_plan_versions WHERE id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return decode_plan_version(row["snapshot"], row["created_at"])

    def get_plan_approval(self, plan_version_id: str) -> PlanApproval | None:
        row = self._connection.execute(
            "SELECT * FROM plan_approvals WHERE plan_version_id = ?",
            (plan_version_id,),
        ).fetchone()
        return PlanApproval(row["plan_version_id"], row["created_at"]) if row else None

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

    def get_provider_job(self, external_job_id: str) -> ProviderJob:
        """A job's sources and action are those of the plan version it ran."""

        row = self._connection.execute(
            "SELECT * FROM provider_jobs WHERE external_job_id = ?",
            (external_job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(external_job_id)
        version = self.get_plan_version(row["plan_version_id"])
        return ProviderJob(
            external_job_id=row["external_job_id"],
            run_id=row["run_id"],
            idempotency_key=row["idempotency_key"],
            action=version.plan.action,
            sources=version.sources,
            created_at=row["created_at"],
            plan_version_id=version.id,
        )

    def list_provider_jobs(self, run_id: str) -> tuple[ProviderJob, ...]:
        rows = self._connection.execute(
            """SELECT external_job_id FROM provider_jobs WHERE run_id = ?
               ORDER BY created_at, external_job_id""",
            (run_id,),
        ).fetchall()
        return tuple(self.get_provider_job(row[0]) for row in rows)

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

    def list_provider_events(self, run_id: str) -> tuple[ProviderEvent, ...]:
        rows = self._connection.execute(
            """SELECT e.external_event_id FROM provider_events e
               JOIN provider_jobs j ON j.external_job_id = e.external_job_id
               WHERE j.run_id = ? ORDER BY e.created_at, e.external_event_id""",
            (run_id,),
        ).fetchall()
        return tuple(self.get_provider_event(row[0]) for row in rows)

    def generated_videos(self, run_id: str) -> tuple[ArtifactVersion, ...]:
        rows = self._connection.execute(
            """SELECT v.id FROM artifact_versions v
               JOIN provider_jobs j ON j.external_job_id = v.external_job_id
               WHERE j.run_id = ? AND v.kind = 'video' ORDER BY v.created_at, v.id""",
            (run_id,),
        ).fetchall()
        return tuple(self.artifacts.get(row[0]) for row in rows)

    def execution_counts(self, run_id: str) -> dict[str, int]:
        """Durable counts; audit rows are not callback delivery totals."""

        self.get(run_id)
        row = self._connection.execute(
            """SELECT
                (SELECT COUNT(*) FROM provider_jobs WHERE run_id = ?) AS jobs,
                (SELECT COUNT(DISTINCT e.external_job_id) FROM provider_events e
                    JOIN provider_jobs j ON j.external_job_id = e.external_job_id
                    WHERE j.run_id = ?) AS completed_jobs,
                (SELECT COUNT(*) FROM provider_events e JOIN provider_jobs j
                    ON j.external_job_id = e.external_job_id WHERE j.run_id = ?) AS events,
                (SELECT COUNT(*) FROM artifact_versions v JOIN provider_jobs j
                    ON j.external_job_id = v.external_job_id WHERE j.run_id = ?
                    AND v.kind = 'video') AS replacements""",
            (run_id, run_id, run_id, run_id),
        ).fetchone()
        return {
            "recorded_provider_jobs": row["jobs"],
            "distinct_jobs_with_completion_reports": row["completed_jobs"],
            "callback_audit_rows": row["events"],
            "replacement_video_versions": row["replacements"],
        }

    def get_job_timing(self, external_job_id: str) -> JobTiming:
        """Earliest completion report; duplicate deliveries cannot extend it."""

        job = self.get_provider_job(external_job_id)
        approval = self.get_plan_approval(job.plan_version_id)
        completed = self._connection.execute(
            "SELECT MIN(created_at) FROM provider_events WHERE external_job_id = ?",
            (external_job_id,),
        ).fetchone()
        claim = self._connection.execute(
            "SELECT first_claimed_at FROM worker_attempts WHERE plan_version_id = ?",
            (job.plan_version_id,),
        ).fetchone()
        return job_timing(
            approval.created_at if approval else None,
            job.created_at,
            completed[0],
            claim[0] if claim else None,
        )


def _first_status(plan: RepairPlan) -> WorkflowStatus:
    if plan.requires_repair_input:
        return WorkflowStatus.NEEDS_REPAIR_INPUT
    return WorkflowStatus.AWAITING_APPROVAL


def _revised_status(current: WorkflowRun, plan: RepairPlan) -> WorkflowStatus:
    """An edit keeps a missing input missing; a new action may need its own."""

    if current.plan is not None and current.plan.action is plan.action:
        if current.status is WorkflowStatus.NEEDS_REPAIR_INPUT:
            return WorkflowStatus.NEEDS_REPAIR_INPUT
        return WorkflowStatus.AWAITING_APPROVAL
    return _first_status(plan)


def _require_current_version(run: WorkflowRun, expected: str | None) -> None:
    """Every reviewer command names the plan version it was decided against.

    None means the run had no plan yet, as when a creative choice is pending.
    """

    if run.plan_version_id != expected:
        raise ValueError(STALE_PLAN)


def _encode_clarification(request: ClarificationRequest) -> str:
    return json.dumps(
        {
            "question": request.question,
            "options": [
                {**asdict(option), "invalidates": sorted(option.invalidates)}
                for option in request.options
            ],
        }
    )


def _decode_clarification(value: str | None) -> ClarificationRequest | None:
    if value is None:
        return None
    data = json.loads(value)
    return ClarificationRequest(
        question=data["question"],
        options=tuple(
            RepairOption(
                action=RepairAction(option["action"]),
                invalidates=frozenset(
                    ArtifactKind(kind) for kind in option["invalidates"]
                ),
                rationale=option["rationale"],
            )
            for option in data["options"]
        ),
    )
