"""Provider boundary and deterministic fake used by the first vertical slice."""

from dataclasses import dataclass
from typing import Protocol


class VideoProvider(Protocol):
    def submit(self, *, idempotency_key: str, action: str) -> str:
        """Submit work or return the existing external job for the same key."""


@dataclass(frozen=True)
class _FakeProviderJob:
    external_job_id: str
    action: str


class FakeVideoProvider:
    """An external provider with an idempotent submit contract.

    The instance represents state owned by a remote service, so it deliberately
    lives independently from an application's repository or executor instance.
    """

    def __init__(self) -> None:
        self._jobs_by_key: dict[str, _FakeProviderJob] = {}
        self.submit_attempts = 0

    @property
    def jobs_created(self) -> int:
        return len(self._jobs_by_key)

    def submit(self, *, idempotency_key: str, action: str) -> str:
        self.submit_attempts += 1
        existing = self._jobs_by_key.get(idempotency_key)
        if existing is not None:
            if existing.action != action:
                raise ValueError("idempotency key was reused for a different action")
            return existing.external_job_id

        external_job_id = f"video-job-{len(self._jobs_by_key) + 1}"
        self._jobs_by_key[idempotency_key] = _FakeProviderJob(
            external_job_id=external_job_id,
            action=action,
        )
        return external_job_id
