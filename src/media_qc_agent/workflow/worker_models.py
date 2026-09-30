"""Durable worker policy and ownership tokens."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerPolicy:
    max_in_flight: int = 2
    max_attempts: int = 3
    lease_seconds: float = 30
    backoff_seconds: float = 2
    max_backoff_seconds: float = 30

    def __post_init__(self) -> None:
        _bounded_integer(self.max_in_flight, 100)
        _bounded_integer(self.max_attempts, 10)
        for value in (
            self.lease_seconds,
            self.backoff_seconds,
            self.max_backoff_seconds,
        ):
            if not math.isfinite(value) or not 0 < value <= 3600:
                raise ValueError(
                    "worker timing must be finite and within 0..3600 seconds"
                )
        if self.max_backoff_seconds < self.backoff_seconds:
            raise ValueError("maximum backoff must be at least the initial backoff")

    def retry_delay(self, attempt: int) -> float:
        return min(self.backoff_seconds * 2 ** (attempt - 1), self.max_backoff_seconds)


def _bounded_integer(value: int, maximum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise ValueError(f"worker limit must be an integer within 1..{maximum}")


@dataclass(frozen=True)
class SubmissionLease:
    run_id: str
    plan_version_id: str
    owner: str
    attempt: int
    expires_at: float


class LeaseLost(RuntimeError):
    """An expired or superseded owner must not mutate another worker's attempt."""


@dataclass(frozen=True)
class WorkerResult:
    outcome: str
    run_id: str | None = None
    external_job_id: str | None = None
