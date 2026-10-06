"""Model-turn spans: version identity and validation outcome, never content.

Spans carry allowlisted attributes and SHA-256 artifact/run references only;
feedback, evidence, explanations, and raw output stay out of telemetry. The
returned turn keeps the raw output for callers that may record synthetic data.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import StrEnum

from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, StatusCode, Tracer

from media_qc_agent.agent.contracts import (
    InterpretationRequest,
    ModelIdentity,
    ModelProvider,
)
from media_qc_agent.agent.interpretation import (
    Interpretation,
    clarification_type,
    repair_scopes,
    validate_interpretation,
)
from media_qc_agent.agent.openrouter import ModelProviderError
from media_qc_agent.domain.ids import reference

# The only attribute shapes these spans emit; keeps the allowlist explicit.
AttributeValue = str | int | bool | tuple[str, ...]


class TurnOutcome(StrEnum):
    ACCEPTED = "accepted"
    ABSTAINED = "abstained"
    REJECTED = "rejected"
    PROVIDER_ERROR = "provider_error"


@dataclass(frozen=True)
class InterpretationTurn:
    """One diagnosis and planning turn; `trace_id` is None when not exported."""

    identity: ModelIdentity
    request: InterpretationRequest
    outcome: TurnOutcome
    raw_output: str | None
    interpretation: Interpretation | None
    trace_id: str | None


def turn_outcome(interpretation: Interpretation) -> TurnOutcome:
    if interpretation.finding is None:
        return TurnOutcome.ABSTAINED
    return TurnOutcome.ACCEPTED


def plan_attributes(interpretation: Interpretation) -> dict[str, AttributeValue]:
    finding = interpretation.finding
    return {
        "agent.outcome": turn_outcome(interpretation).value,
        "agent.failure_kind": finding.kind.value if finding else "none",
        "agent.repair_actions": tuple(
            sorted(action.value for action in repair_scopes(interpretation))
        ),
        "agent.clarification": clarification_type(interpretation),
        "agent.cited_evidence": len(interpretation.evidence),
    }


def turn_attributes(
    identity: ModelIdentity, request: InterpretationRequest, run_id: str | None
) -> dict[str, AttributeValue]:
    attributes: dict[str, AttributeValue] = {
        "gen_ai.operation.name": "interpret",
        "gen_ai.provider.name": identity.provider,
        "gen_ai.request.model": identity.model,
        "agent.prompt.version": identity.prompt_version,
        "agent.artifact_refs": tuple(
            reference(version) or "" for version in request.artifact_version_ids
        ),
    }
    if run_id is not None:
        attributes["workflow.run_ref"] = reference(run_id) or ""
    return attributes


@contextmanager
def safe_span(
    tracer: Tracer, name: str, kind: SpanKind = SpanKind.INTERNAL
) -> Iterator[Span | None]:
    """A current span whose failures, and body exceptions, are never recorded."""

    manager = None
    span = None
    try:
        manager = tracer.start_as_current_span(
            name, kind=kind, record_exception=False, set_status_on_exception=False
        )
        span = manager.__enter__()
    except Exception:  # noqa: BLE001 — tracing must not change the diagnosis
        manager = None
    try:
        yield span
    finally:
        if manager is not None:
            with suppress(Exception):
                manager.__exit__(None, None, None)


def annotate(span: Span | None, attributes: Mapping[str, AttributeValue]) -> None:
    if span is not None:
        with suppress(Exception):
            span.set_attributes(attributes)


def _fail(span: Span | None, error_type: str) -> None:
    annotate(span, {"error.type": error_type})
    if span is not None:
        with suppress(Exception):
            span.set_status(StatusCode.ERROR)


def _trace_id(span: Span | None) -> str | None:
    if span is None:
        return None
    with suppress(Exception):
        context = span.get_span_context()
        if context.is_valid:
            return f"{context.trace_id:032x}"
    return None


def _diagnose(
    tracer: Tracer, provider: ModelProvider, request: InterpretationRequest
) -> str | None:
    with safe_span(tracer, "agent.diagnose", SpanKind.CLIENT) as span:
        try:
            return provider.interpret(request)
        except (ValueError, ModelProviderError):
            _fail(span, "provider_error")
            return None


def _plan(
    tracer: Tracer, raw: str, request: InterpretationRequest
) -> Interpretation | None:
    with safe_span(tracer, "agent.plan") as span:
        try:
            interpretation = validate_interpretation(raw, request)
        except ValueError:
            annotate(span, {"agent.outcome": TurnOutcome.REJECTED.value})
            _fail(span, "validation_rejected")
            return None
        annotate(span, plan_attributes(interpretation))
        return interpretation


def interpret_traced(
    provider: ModelProvider,
    request: InterpretationRequest,
    *,
    tracer: Tracer | None = None,
    run_id: str | None = None,
) -> InterpretationTurn:
    """Interpret once, tracing the model call and validation/policy separately.

    Pass `run_id` when the turn's finding creates or updates that workflow run.
    Provider and validation errors become outcomes, not exceptions.
    """

    tracer = tracer or trace.get_tracer("media_qc_agent.agent")
    identity = provider.identity
    with safe_span(tracer, "agent.interpret") as span:
        annotate(span, turn_attributes(identity, request, run_id))
        raw = _diagnose(tracer, provider, request)
        interpretation = None if raw is None else _plan(tracer, raw, request)
        if raw is None:
            outcome = TurnOutcome.PROVIDER_ERROR
        elif interpretation is None:
            outcome = TurnOutcome.REJECTED
        else:
            outcome = turn_outcome(interpretation)
        annotate(span, {"agent.outcome": outcome.value})
        return InterpretationTurn(
            identity, request, outcome, raw, interpretation, _trace_id(span)
        )
