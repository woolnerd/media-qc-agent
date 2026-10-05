"""Pure timing calculations from durable UTC workflow timestamps."""

from dataclasses import dataclass
from datetime import datetime


def elapsed_seconds(start: str | None, end: str | None) -> float | None:
    """Missing or reversed timestamps are unknown, not zero latency."""
    if start is None or end is None:
        return None
    duration = (
        datetime.fromisoformat(end) - datetime.fromisoformat(start)
    ).total_seconds()
    return duration if duration >= 0 else None


@dataclass(frozen=True)
class JobTiming:
    approval_to_completion_seconds: float | None
    recorded_job_to_completion_seconds: float | None
    approval_to_first_claim_seconds: float | None = None


def job_timing(
    approval_at: str | None,
    job_recorded_at: str,
    completed_at: str | None,
    first_claimed_at: str | None = None,
) -> JobTiming:
    return JobTiming(
        elapsed_seconds(approval_at, completed_at),
        elapsed_seconds(job_recorded_at, completed_at),
        elapsed_seconds(approval_at, first_claimed_at),
    )
