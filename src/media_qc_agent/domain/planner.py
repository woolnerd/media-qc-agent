"""Pure repair-policy decisions.

An LLM may eventually classify ambiguous review feedback into a QualityFinding,
but it does not get unrestricted authority to choose arbitrary side effects.
This module defines the safe, testable action boundary.
"""

from media_qc_agent.domain.models import (
    ArtifactKind,
    ClarificationRequest,
    FailureKind,
    QualityFinding,
    RepairAction,
    RepairOption,
    RepairPlan,
)


def plan_repair(finding: QualityFinding) -> RepairPlan | ClarificationRequest:
    """Return a concrete repair or a question requiring human direction."""

    if finding.kind is FailureKind.SCRIPT_QUALITY:
        return RepairPlan(
            action=RepairAction.REVISE_SCRIPT,
            invalidates=frozenset(
                {
                    ArtifactKind.SCRIPT,
                    ArtifactKind.TTS_INPUT,
                    ArtifactKind.VIDEO,
                    ArtifactKind.CAPTIONS,
                }
            ),
            requires_repair_input=True,
            rationale=(
                "Revise and approve the script and its derived TTS input before "
                "regenerating video and captions. Preserve the avatar and voice."
            ),
        )

    if finding.kind is FailureKind.ENVIRONMENT_MISMATCH:
        return ClarificationRequest(
            question=(
                "Should the script be revised to fit the avatar environment, "
                "or should the avatar be changed to fit the script?"
            ),
            options=(
                RepairOption(
                    action=RepairAction.REVISE_SCRIPT,
                    invalidates=frozenset(
                        {
                            ArtifactKind.SCRIPT,
                            ArtifactKind.TTS_INPUT,
                            ArtifactKind.VIDEO,
                            ArtifactKind.CAPTIONS,
                        }
                    ),
                    rationale=(
                        "Revise the script and derived TTS input, then regenerate "
                        "their descendants; "
                        "preserve the avatar and voice."
                    ),
                ),
                RepairOption(
                    action=RepairAction.CHANGE_AVATAR,
                    invalidates=frozenset(
                        {ArtifactKind.AVATAR, ArtifactKind.VIDEO, ArtifactKind.CAPTIONS}
                    ),
                    rationale=(
                        "Change the avatar and regenerate its descendants; "
                        "preserve the script and voice."
                    ),
                ),
            ),
        )

    if finding.kind is FailureKind.TTS_INPUT_COMPATIBILITY:
        return RepairPlan(
            action=RepairAction.REPAIR_TTS_INPUT,
            invalidates=frozenset(
                {ArtifactKind.TTS_INPUT, ArtifactKind.VIDEO, ArtifactKind.CAPTIONS}
            ),
            requires_repair_input=True,
            rationale=(
                "Prepare and validate replacement provider-facing spoken text "
                "for the selected TTS model before approval. Preserve the "
                "authored script, avatar, and voice profile; regenerate any "
                "video and captions produced from the unsafe input."
            ),
        )

    if finding.kind is FailureKind.CAPTION_FORMAT:
        return RepairPlan(
            action=RepairAction.REPAIR_CAPTIONS,
            invalidates=frozenset({ArtifactKind.CAPTIONS}),
            requires_repair_input=False,
            rationale=(
                "Repair and validate the caption artifact without paying to "
                "regenerate an otherwise acceptable video."
            ),
        )

    if finding.kind is FailureKind.VISUAL_QUALITY:
        return RepairPlan(
            action=RepairAction.REGENERATE_VIDEO,
            invalidates=frozenset({ArtifactKind.VIDEO, ArtifactKind.CAPTIONS}),
            requires_repair_input=False,
            rationale=(
                "After human approval, retry video generation with the approved "
                "script, avatar, and voice; then regenerate captions from the "
                "replacement video."
            ),
        )

    raise ValueError(f"unsupported failure kind: {finding.kind}")


def select_repair(request: ClarificationRequest, action: RepairAction) -> RepairPlan:
    """Turn a human's offered choice into a concrete plan."""

    for option in request.options:
        if option.action is action:
            return RepairPlan(
                action=option.action,
                invalidates=option.invalidates,
                requires_repair_input=True,
                rationale=option.rationale,
            )
    raise ValueError(f"repair action {action} was not offered")
