from __future__ import annotations

from agent.interaction.admission import admit_interaction
from agent.interaction.continue_intent import DirectTaskResumeGuard, ResumeClassification
from agent.interaction.types import (
    ActionGrounding,
    InteractionAction,
    InteractionAmbiguity,
    InteractionBoundary,
    InteractionModelDecision,
)


def test_resume_with_new_read_condition_is_override() -> None:
    assert DirectTaskResumeGuard.classify("Resume the previous task; read only") is ResumeClassification.OVERRIDE
    assert DirectTaskResumeGuard.classify(
        "Resume the previous task. Delete parser.py."
    ) is ResumeClassification.OVERRIDE
    assert DirectTaskResumeGuard.classify("Retome a tarefa anterior, mas não altere nada") is ResumeClassification.OVERRIDE


def test_resume_override_projects_to_the_exact_admission_reason() -> None:
    subject = "Resume the previous task. Delete parser.py."
    resolution = admit_interaction(
        boundary=InteractionBoundary.NATURAL,
        visible_user_text=subject,
        subject=subject,
        model_decision=InteractionModelDecision(
            action=InteractionAction.CONTINUE,
            directive=None,
            ambiguity=InteractionAmbiguity.NONE,
            grounding=ActionGrounding.CURRENT_TURN,
            operation_requested=False,
            proposal_only=False,
            resume_requested=True,
            evidence=subject,
        ),
    )
    assert resolution.action is InteractionAction.CLARIFY
    assert resolution.ambiguity is InteractionAmbiguity.CONFLICT
    assert resolution.reason_code == "INTERACTION_RESUME_OVERRIDE_FORBIDDEN"
