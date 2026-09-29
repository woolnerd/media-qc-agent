"""Interpret a synthetic review offline, or explicitly through OpenRouter."""

import argparse
import json
from dataclasses import asdict

from media_qc_agent.agent.contracts import (
    FakeModelProvider,
    InterpretationRequest,
    ModelProvider,
)
from media_qc_agent.agent.interpretation import Interpretation, interpret_feedback
from media_qc_agent.agent.openrouter import OpenRouterModelProvider
from media_qc_agent.domain.evidence import EvidenceInput, EvidenceRole


def synthetic_request(
    video_version_id: str = "video:observed-fixture",
) -> InterpretationRequest:
    return InterpretationRequest(
        "The subject jumps abruptly in the same shot; the video looks jerky.",
        (video_version_id,),
        (
            EvidenceInput(
                EvidenceRole.FACT,
                video_version_id,
                "Two same-shot displacement jumps exceed the synthetic limit",
                "25 px/frame",
                "12 px/frame",
            ),
        ),
    )


def synthetic_provider(request: InterpretationRequest) -> FakeModelProvider:
    return FakeModelProvider(
        {
            request: json.dumps(
                {
                    "kind": "visual_quality",
                    "explanation": "Synthetic same-shot motion jumps suggest jerky video",
                    "confidence": 0.95,
                    "evidence_indices": [0],
                    "action": "regenerate_video",
                    "invalidates": ["video", "captions"],
                }
            )
        }
    )


def run_review_demo(provider: ModelProvider | None = None) -> Interpretation:
    request = synthetic_request()
    return interpret_feedback(provider or synthetic_provider(request), request)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Make one paid OpenRouter interpretation request",
    )
    args = parser.parse_args()
    provider = OpenRouterModelProvider.from_environment() if args.live else None
    print(
        json.dumps(
            asdict(run_review_demo(provider)),
            default=lambda value: sorted(value),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
