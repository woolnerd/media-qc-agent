"""Pure, escaped presentation of persistent workflow and recovery evidence."""

from media_qc_agent.api.html_parts import document, notice, panel, run_url, table, text
from media_qc_agent.api.review_data import ReviewSnapshot
from media_qc_agent.api.review_forms import callback_forms, decision_forms, worker_forms
from media_qc_agent.api.scenarios import DEMO_NOTICE, SCENARIOS
from media_qc_agent.workflow.models import WorkflowRun, WorkflowStatus

STATE_HELP = {
    "needs_input": "Choose whether to revise the script or change the avatar.",
    "needs_repair_input": "Bind the exact replacement input before approval.",
    "awaiting_approval": "Review this plan and its inputs, then approve its exact version.",
    "ready": "Approved inputs are ready for execution.",
    "submitting": "Submission is reserved. Inputs remain locked while acceptance is resolved.",
    "submitted": "The provider job is recorded. Completion will create its replacement version.",
    "succeeded": "Review the accepted replacement and its exact lineage.",
}


def dashboard(
    runs: tuple[WorkflowRun, ...], message: str = "", error: bool = False
) -> str:
    options = "".join(
        f'<option value="{text(item.id)}">{text(item.title)}</option>'
        for item in SCENARIOS
    )
    create = f"""<form method="post" action="/review/create"><label>Scenario<select name="scenario_id">{options}</select></label>
        <label>Run ID<input name="run_id" placeholder="review-1" pattern="[a-z0-9]+(-[a-z0-9]+)*" maxlength="80" required></label>
        <p class="hint">Lowercase letters, numbers, and hyphens. Each run keeps its own review history.</p><button>Create scenario</button></form>"""
    links = "".join(
        f'<a class="run-row" href="{text(run_url(run.id))}"><strong>{text(run.id)}</strong><span>{text(run.status.value.replace("_", " "))}</span></a>'
        for run in runs
    )
    body = _intro(
        "Review repairs. Inspect recovery.",
        "Choose a synthetic scenario, review its evidence, and approve the minimum repair.",
    )
    body += (
        notice(message, error)
        + f'<div class="grid">{panel("Start a scenario", create)}{panel("Recent runs", links or "<p>No runs yet. Create a scenario to start.</p>")}</div>'
    )
    return document("Scenarios & runs", body)


def review_page(data: ReviewSnapshot, message: str = "", error: bool = False) -> str:
    run = data.run
    state = run.status.value
    header = f'<div class="heading"><div><p class="eyebrow">Workflow review</p><h1>{text(run.id)}</h1></div><span class="badge {text(state)}">{text(state.replace("_", " "))}</span></div>'
    header += f'<p class="lead">{text(STATE_HELP[state])}</p><p class="demo-notice">{text(DEMO_NOTICE)}</p>'
    header += f'<a class="refresh" href="{text(run_url(run.id))}">Refresh state</a>'
    body = header + notice(message, error) + _proof(data)
    body += (
        '<div class="grid"><div>'
        + _finding(data)
        + _artifacts(data)
        + "</div><div>"
        + decision_forms(data)
        + worker_forms(data)
        + callback_forms(data)
        + "</div></div>"
    )
    body += _history(data) + _events(data)
    return document(run.id, body, refresh=run.status is WorkflowStatus.SUBMITTING)


def _intro(title: str, subtitle: str) -> str:
    return f'<p class="eyebrow">Evidence → decision → recovery</p><h1>{text(title)}</h1><p class="lead">{text(subtitle)}</p><p class="demo-notice">{text(DEMO_NOTICE)}</p>'


def _proof(data: ReviewSnapshot) -> str:
    accepted = int(data.accepted_job is not None)
    count = data.replacement_count
    if data.run.plan and data.run.plan.action.value == "repair_captions":
        return panel(
            "Accepted video preserved",
            f'<p>Caption-only repair creates no video job. Review the active caption and video lineage below.</p><p class="hint">Caption: <code>{text(data.run.active_caption_version_id or "not yet repaired")}</code></p>',
            style="proof-panel",
        )
    detail = f'<p class="hint">Accepted job: <code>{text(data.accepted_job or "not yet accepted")}</code></p>'
    counters = f'<div class="proof"><div><strong>{accepted}</strong><span>accepted job for the current key</span></div><div><strong data-testid="replacement-count">{count}</strong><span>replacement video for this plan</span></div></div>'
    title = "Single replacement confirmed" if count == 1 else "Recovery evidence"
    return panel(title, counters + detail, style="proof-panel")


def _finding(data: ReviewSnapshot) -> str:
    content = f'<p class="eyebrow">{text(data.finding.kind.value.replace("_", " "))}</p><p>{text(data.finding.explanation)}</p>'
    content += f'<p class="hint">Recorded confidence {data.finding.confidence:.2f}; synthetic signal, not calibrated quality assurance.</p>'
    for item in data.evidence:
        content += f'<article class="evidence"><span class="tag">{text(item.role.value)}</span><p>{text(item.statement)}</p><small>{text(item.artifact_version_id)}</small>'
        if item.observed is not None:
            content += f'<p class="hint">Observed: {text(item.observed)} · Limit: {text(item.limit or "not specified")}</p>'
        content += "</article>"
    return panel("Finding & evidence", content)


def _artifacts(data: ReviewSnapshot) -> str:
    rows = (
        (
            artifact.kind.value.replace("_", " "),
            artifact.id,
            description,
            "\n".join(
                f"{kind.value}: {version}" for kind, version in artifact.source_versions
            )
            or "Source artifact",
        )
        for artifact, description in zip(data.artifacts, data.descriptions, strict=True)
    )
    return panel(
        "Artifact versions & lineage",
        table(("Kind", "Version", "Content / declaration", "Derived from"), rows),
    )


def _history(data: ReviewSnapshot) -> str:
    rows = (
        (
            version.revision,
            version.id,
            version.plan.action.value,
            version.plan.rationale,
            approval.created_at if approval else "Not approved",
        )
        for version, approval in zip(data.versions, data.approvals, strict=True)
    )
    current = f"<p>Current plan: <code>{text(data.run.plan_version_id or 'creative choice required')}</code></p>"
    details = f"<details><summary>Recovery key and all generated versions</summary><code>{text(data.run.idempotency_key or 'not assigned')}</code>"
    details += (
        table(
            ("Video version", "Exact inputs"),
            (
                (
                    video.id,
                    "\n".join(
                        f"{kind.value}: {version}"
                        for kind, version in video.source_versions
                    ),
                )
                for video in data.videos
            ),
        )
        + "</details>"
    )
    return panel(
        "Immutable plan history",
        current
        + table(("Revision", "Plan version", "Action", "Rationale", "Approval"), rows)
        + details,
    )


def _events(data: ReviewSnapshot) -> str:
    rows = (
        (
            event.external_event_id,
            event.external_job_id,
            event.disposition.value,
            event.reason or "Recorded completion",
        )
        for event in data.events
    )
    return panel(
        "Callback audit",
        table(("Event", "Provider job", "Disposition", "Reason"), rows)
        if data.events
        else '<p class="hint">No completion events yet.</p>',
    )
