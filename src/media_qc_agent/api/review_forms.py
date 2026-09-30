"""State-aware forms; repository commands remain the mutation authority."""

from media_qc_agent.api.html_parts import form, panel, text
from media_qc_agent.api.review_data import ReviewSnapshot
from media_qc_agent.domain.models import ArtifactKind, RepairAction
from media_qc_agent.workflow.models import WorkflowRun, WorkflowStatus

EDITABLE = {
    WorkflowStatus.NEEDS_REPAIR_INPUT,
    WorkflowStatus.AWAITING_APPROVAL,
    WorkflowStatus.READY,
}
REPLACEMENTS = {
    RepairAction.REVISE_SCRIPT: (("script-api-revised", "Clearer neutral script"),),
    RepairAction.REPAIR_TTS_INPUT: (
        ("tts-api-replacement", "Replacement spoken text"),
    ),
    RepairAction.CHANGE_AVATAR: (("avatar-api-kitchen", "Kitchen avatar"),),
}


def decision_forms(data: ReviewSnapshot) -> str:
    run = data.run
    if run.clarification is not None:
        options = "".join(
            form(
                run,
                "choose",
                option.action.value.replace("_", " ").title(),
                value=option.action.value,
            )
            for option in run.clarification.options
        )
        return panel(
            "Choose the repair",
            f"<p>{text(run.clarification.question)}</p><div class='actions'>{options}</div>",
        )
    if run.plan is None:
        return ""
    scope = ", ".join(
        sorted(kind.value.replace("_", " ") for kind in run.plan.invalidates)
    )
    content = f'<p class="eyebrow">{text(run.plan.action.value.replace("_", " "))}</p><p>{text(run.plan.rationale)}</p><p><strong>Invalidates:</strong> {text(scope)}</p>'
    content += _binding_forms(run) + _review_form(data)
    return panel("Plan & exact approval", content)


def _binding_forms(run: WorkflowRun) -> str:
    if run.plan is None or run.status not in EDITABLE:
        return ""
    choices = REPLACEMENTS.get(run.plan.action, ())
    options = "".join(
        f'<option value="{text(version)}">{text(label)} · {text(version)}</option>'
        for version, label in choices
    )
    result = ""
    if choices:
        fields = (
            f'<label>Replacement input<select name="value">{options}</select></label>'
        )
        bound = dict(run.sources.dependencies())
        kind = {
            RepairAction.REVISE_SCRIPT: ArtifactKind.SCRIPT,
            RepairAction.REPAIR_TTS_INPUT: ArtifactKind.TTS_INPUT,
            RepairAction.CHANGE_AVATAR: ArtifactKind.AVATAR,
        }[run.plan.action]
        result = form(
            run,
            "bind",
            "Bind replacement",
            fields=fields,
            disabled=all(version == bound[kind] for version, _ in choices),
        )
    if run.plan.action is RepairAction.REVISE_SCRIPT:
        result += form(
            run,
            "tts",
            "Bind matching TTS input",
            value="tts-api-revised",
            disabled=run.sources.script_version_id != "script-api-revised"
            or run.sources.tts_input_version_id == "tts-api-revised",
            style="secondary",
        )
    return result


def _review_form(data: ReviewSnapshot) -> str:
    run = data.run
    if run.plan is None or run.status not in EDITABLE:
        return _approval_summary(run)
    fields = f'<label>Plan rationale<textarea name="value" maxlength="4000" required>{text(run.plan.rationale)}</textarea></label>'
    result = (
        "<details><summary>Edit rationale (requires new approval)</summary>"
        + form(run, "edit", "Save new plan revision", fields=fields, style="secondary")
        + "</details>"
    )
    result += form(
        run,
        "approve",
        "Approve this exact plan",
        disabled=run.status is not WorkflowStatus.AWAITING_APPROVAL
        or not _matching_tts(data),
    )
    if not _matching_tts(data):
        result += '<p class="hint">Bind a TTS input derived from the current script before approval.</p>'
    result += _approval_summary(run)
    if (
        run.plan.action is RepairAction.REPAIR_CAPTIONS
        and run.status is WorkflowStatus.READY
    ):
        fields = '<label>Corrected caption text<textarea name="value" maxlength="1000" required>Fixed synthetic caption.</textarea></label><p class="hint">One demo cue, 0–1000 ms. Caption rules are validated before promotion.</p>'
        result += form(run, "captions", "Repair captions locally", fields=fields)
    return result


def _matching_tts(data: ReviewSnapshot) -> bool:
    tts = next(
        artifact
        for artifact in data.artifacts
        if artifact.id == data.run.sources.tts_input_version_id
    )
    return (
        ArtifactKind.SCRIPT,
        data.run.sources.script_version_id,
    ) in tts.source_versions


def _approval_summary(run: WorkflowRun) -> str:
    if run.approval is None:
        return '<p class="hint">No current approval. Changes require review of the new plan version.</p>'
    return f'<p class="approved">Approved exact version <code>{text(run.approval.plan_version_id)}</code></p>'


def worker_forms(data: ReviewSnapshot) -> str:
    run = data.run
    if run.plan is None or run.plan.action is RepairAction.REPAIR_CAPTIONS:
        return ""
    info = data.recovery
    ready = (
        run.status is WorkflowStatus.READY
        and info.capacity_used < info.policy.max_in_flight
    )
    recoverable = (
        run.status is WorkflowStatus.SUBMITTING
        and info.lease_remaining == 0
        and info.retry_remaining == 0
        and not info.stopped
    )
    controls = form(run, "submit", "Run worker step", disabled=not ready)
    controls += form(
        run,
        "interrupt",
        "Interrupt after acceptance",
        disabled=not ready,
        style="warning",
    )
    controls += form(
        run,
        "recover",
        "Recover interrupted submission",
        disabled=not recoverable,
        style="secondary",
    )
    controls += form(
        run,
        "retry",
        "Request a new attempt",
        disabled=run.status not in {WorkflowStatus.SUBMITTED, WorkflowStatus.SUCCEEDED},
        style="secondary",
    )
    status = f"<dl><dt>Recovery claims</dt><dd>{info.attempts} / {info.policy.max_attempts}</dd><dt>Lease remaining</dt><dd>{info.lease_remaining} seconds</dd><dt>Backoff remaining</dt><dd>{info.retry_remaining} seconds</dd><dt>Shared capacity</dt><dd>{info.capacity_used} / {info.policy.max_in_flight}</dd></dl>"
    if info.stopped:
        status += '<p class="notice error">Automatic recovery stopped. The original outcome needs reconciliation; capacity remains reserved.</p>'
    return panel(
        "Recovery controls",
        '<p class="hint">Synthetic fault harness. For a controlled interruption, pause the separate worker and use these steps.</p><div class="actions">'
        + controls
        + "</div>"
        + status,
    )


def callback_forms(data: ReviewSnapshot) -> str:
    seen = {event.external_job_id for event in data.events}
    rows = ""
    for job in data.jobs:
        rows += (
            '<div class="job"><code>'
            + text(job.external_job_id)
            + '</code><div class="actions">'
        )
        rows += form(
            data.run,
            "complete",
            "Complete synthetic job",
            value=job.external_job_id,
            disabled=job.external_job_id in seen,
        )
        rows += (
            form(
                data.run,
                "duplicate",
                "Replay duplicate callback",
                value=job.external_job_id,
                disabled=job.external_job_id not in seen,
                style="secondary",
            )
            + "</div></div>"
        )
    return panel(
        "Completion controls",
        rows or '<p class="hint">Available once the worker records a provider job.</p>',
    )
