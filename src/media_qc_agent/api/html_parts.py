"""Small escaped HTML primitives; no client-side state machine."""

from collections.abc import Iterable
from html import escape
from urllib.parse import quote

from media_qc_agent.workflow.models import WorkflowRun


def text(value: object) -> str:
    return escape(str(value), quote=True)


def run_url(run_id: str) -> str:
    return "/review/" + quote(run_id, safe="")


def table(headings: tuple[str, ...], rows: Iterable[tuple[object, ...]]) -> str:
    header = "".join(f'<th scope="col">{text(heading)}</th>' for heading in headings)
    body = "".join(
        "<tr>" + "".join(f"<td>{text(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f'<div class="table-scroll"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>'


def panel(title: str, content: str, *, style: str = "") -> str:
    return f'<section class="panel {style}"><h2>{text(title)}</h2>{content}</section>'


def document(title: str, body: str, *, refresh: bool = False) -> str:
    meta = '<meta http-equiv="refresh" content="2">' if refresh else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">{meta}
    <title>{text(title)} · Media QC</title><link rel="stylesheet" href="/assets/review.css"></head>
    <body><header class="topbar"><a class="brand" href="/">MEDIA QC <span>review lab</span></a>
    <nav aria-label="Main"><a href="/">Scenarios &amp; runs</a><a href="/docs">API docs</a></nav></header>
    <main>{body}</main><footer>Local synthetic demo · No paid provider calls · Quality signals are illustrative</footer></body></html>"""


def form(
    run: WorkflowRun,
    action: str,
    label: str,
    *,
    fields: str = "",
    value: str | None = None,
    disabled: bool = False,
    style: str = "",
) -> str:
    version = ""
    if run.plan_version_id is not None:
        version = f'<input type="hidden" name="expected_plan_version_id" value="{text(run.plan_version_id)}">'
    hidden_value = (
        f'<input type="hidden" name="value" value="{text(value)}">'
        if value is not None
        else ""
    )
    blocked = "disabled" if disabled else ""
    return f'''<form method="post" action="{text(run_url(run.id))}/action">
    <input type="hidden" name="action" value="{text(action)}">{version}{hidden_value}{fields}
    <button class="{style}" type="submit" {blocked}>{text(label)}</button></form>'''


def notice(message: str, error: bool) -> str:
    if not message:
        return ""
    return f'<div class="notice {"error" if error else ""}" role="alert">{text(message)}</div>'
