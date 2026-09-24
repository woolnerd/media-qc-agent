import unittest

from media_qc_agent import (
    ArtifactKind,
    ClarificationRequest,
    FailureKind,
    QualityFinding,
    RepairAction,
    RepairPlan,
    plan_repair,
)


def finding(kind: FailureKind) -> QualityFinding:
    return QualityFinding(
        kind=kind,
        explanation="Synthetic evaluation finding",
        confidence=0.9,
    )


class PlanRepairTests(unittest.TestCase):
    def test_bad_script_invalidates_only_script_and_its_outputs(self) -> None:
        plan = plan_repair(finding(FailureKind.SCRIPT_QUALITY))
        assert isinstance(plan, RepairPlan)

        self.assertEqual(plan.action, RepairAction.REVISE_SCRIPT)
        self.assertEqual(
            plan.invalidates,
            {ArtifactKind.SCRIPT, ArtifactKind.VIDEO, ArtifactKind.CAPTIONS},
        )
        self.assertNotIn(ArtifactKind.AVATAR, plan.invalidates)
        self.assertNotIn(ArtifactKind.VOICE, plan.invalidates)
        self.assertTrue(plan.requires_repair_input)

    def test_environment_mismatch_offers_two_concrete_repairs(self) -> None:
        request = plan_repair(finding(FailureKind.ENVIRONMENT_MISMATCH))

        assert isinstance(request, ClarificationRequest)
        self.assertEqual(
            {option.action for option in request.options},
            {RepairAction.REVISE_SCRIPT, RepairAction.CHANGE_AVATAR},
        )
        self.assertEqual(
            next(
                option
                for option in request.options
                if option.action is RepairAction.REVISE_SCRIPT
            ).invalidates,
            {ArtifactKind.SCRIPT, ArtifactKind.VIDEO, ArtifactKind.CAPTIONS},
        )
        self.assertEqual(
            next(
                option
                for option in request.options
                if option.action is RepairAction.CHANGE_AVATAR
            ).invalidates,
            {ArtifactKind.AVATAR, ArtifactKind.VIDEO, ArtifactKind.CAPTIONS},
        )

    def test_caption_failure_does_not_regenerate_video(self) -> None:
        plan = plan_repair(finding(FailureKind.CAPTION_FORMAT))
        assert isinstance(plan, RepairPlan)

        self.assertEqual(plan.action, RepairAction.REPAIR_CAPTIONS)
        self.assertEqual(plan.invalidates, {ArtifactKind.CAPTIONS})
        self.assertNotIn(ArtifactKind.VIDEO, plan.invalidates)
        self.assertFalse(plan.requires_repair_input)

    def test_visual_failure_preserves_approved_inputs(self) -> None:
        plan = plan_repair(finding(FailureKind.VISUAL_QUALITY))
        assert isinstance(plan, RepairPlan)

        self.assertEqual(plan.action, RepairAction.REGENERATE_VIDEO)
        self.assertEqual(
            plan.invalidates,
            {ArtifactKind.VIDEO, ArtifactKind.CAPTIONS},
        )
        self.assertNotIn(ArtifactKind.SCRIPT, plan.invalidates)
        self.assertNotIn(ArtifactKind.AVATAR, plan.invalidates)
        self.assertNotIn(ArtifactKind.VOICE, plan.invalidates)
        self.assertFalse(plan.requires_repair_input)


class QualityFindingTests(unittest.TestCase):
    def test_rejects_blank_explanation(self) -> None:
        with self.assertRaisesRegex(ValueError, "explanation"):
            QualityFinding(
                kind=FailureKind.SCRIPT_QUALITY,
                explanation="  ",
                confidence=0.8,
            )

    def test_rejects_confidence_outside_probability_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "confidence"):
            QualityFinding(
                kind=FailureKind.SCRIPT_QUALITY,
                explanation="Weak script",
                confidence=1.1,
            )


if __name__ == "__main__":
    unittest.main()
