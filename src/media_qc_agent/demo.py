"""Run one synthetic provider retry and inspect the resulting lineage."""

import json
import sqlite3

from .domain import ArtifactKind
from .environment import Environment, ScriptScene
from .executor import WorkflowExecutor
from .interpretation import interpret_feedback
from .provider import FakeVideoProvider
from .repository import WorkflowRepository
from .review_demo import synthetic_provider, synthetic_request
from .spoken_text import SpokenTextCapabilities
from .workflow import VideoSources


def run_demo() -> dict[str, object]:
    connection = sqlite3.connect(":memory:")
    try:
        repository = WorkflowRepository(connection)
        repository.initialize()
        sources = VideoSources("script-1", "tts-1", "avatar-1", "voice-1")
        repository.create_script_version(
            version_id="script-1",
            authored_text="Heat to 450°F.",
            scene=ScriptScene(Environment.KITCHEN, evidence_phrase="Heat"),
        )
        repository.create_avatar_version(
            version_id="avatar-1", environment=Environment.KITCHEN
        )
        for kind, version_id in sources.dependencies():
            if kind in {
                ArtifactKind.SCRIPT,
                ArtifactKind.TTS_INPUT,
                ArtifactKind.AVATAR,
            }:
                continue
            repository.create_source_version(version_id=version_id, kind=kind)
        repository.create_tts_input_version(
            version_id="tts-1",
            script_version_id="script-1",
            capabilities=SpokenTextCapabilities(
                "synthetic-tts", "literal-v1", frozenset()
            ),
        )
        observed_video_id = repository.create_synthetic_video_version(
            fixture_job_id="observed-fixture", sources=sources
        ).id
        request = synthetic_request(observed_video_id)
        interpreted = interpret_feedback(synthetic_provider(request), request)
        assert interpreted.finding is not None
        repository.create(
            run_id="demo-run",
            finding=interpreted.finding,
            sources=sources,
            observed_artifact_version_id=observed_video_id,
            evidence=interpreted.evidence,
        )
        provider = FakeVideoProvider()
        executor = WorkflowExecutor(repository=repository, provider=provider)

        repository.approve(
            "demo-run", plan_version_id=repository.get("demo-run").plan_version_id
        )
        old_job = executor.submit("demo-run").external_job_id
        assert old_job is not None
        repository.request_retry("demo-run")
        repository.approve(
            "demo-run", plan_version_id=repository.get("demo-run").plan_version_id
        )
        new_job = executor.submit("demo-run").external_job_id
        assert new_job is not None

        repository.record_completion(
            external_job_id=old_job, external_event_id="old-completion"
        )
        repository.record_completion(
            external_job_id=new_job, external_event_id="new-completion"
        )
        run = repository.get("demo-run")
        assert run.active_video_version_id is not None
        video = repository.get_artifact_version(run.active_video_version_id)
        caption = repository.record_caption_version(
            version_id="caption-1", video_version_id=video.id
        )
        return {
            "status": run.status.value,
            "old_event": repository.get_provider_event(
                "old-completion"
            ).disposition.value,
            "new_event": repository.get_provider_event(
                "new-completion"
            ).disposition.value,
            "active_video": video.id,
            "video_sources": {
                kind.value: version_id for kind, version_id in video.source_versions
            },
            "caption_source_video": caption.source_versions[0][1],
        }
    finally:
        connection.close()


def main() -> None:
    print(json.dumps(run_demo(), indent=2))


if __name__ == "__main__":
    main()
