from __future__ import annotations

import pytest

from agent.interaction.admission import admit_interaction
from agent.interaction.types import (
    ActionGrounding,
    InteractionAction,
    InteractionAmbiguity,
    InteractionBoundary,
    InteractionModelDecision,
)


def test_nonempty_clarify_evidence_must_be_a_current_turn_substring() -> None:
    decision = InteractionModelDecision(
        action=InteractionAction.CLARIFY,
        directive=None,
        ambiguity=InteractionAmbiguity.EFFECT,
        grounding=ActionGrounding.CURRENT_TURN,
        operation_requested=True,
        proposal_only=False,
        resume_requested=False,
        evidence="not in current turn",
    )
    with pytest.raises(ValueError, match="INTERACTION_EVIDENCE_MISMATCH"):
        admit_interaction(
            boundary=InteractionBoundary.NATURAL,
            visible_user_text="hello",
            subject="hello",
            model_decision=decision,
        )


@pytest.mark.parametrize(
    ("subject", "evidence"),
    [
        ('Explain "delete parser.py"', "delete parser.py"),
        ("The phrase run tests appears in the README.", "run tests"),
    ],
)
def test_quoted_and_meta_clarify_evidence_is_exact_but_need_not_be_plain(
    subject: str,
    evidence: str,
) -> None:
    decision = InteractionModelDecision(
        action=InteractionAction.CLARIFY,
        directive=None,
        ambiguity=InteractionAmbiguity.EFFECT,
        grounding=ActionGrounding.CURRENT_TURN,
        operation_requested=True,
        proposal_only=False,
        resume_requested=False,
        evidence=evidence,
    )
    resolution = admit_interaction(
        boundary=InteractionBoundary.NATURAL,
        visible_user_text=subject,
        subject=subject,
        model_decision=decision,
    )
    assert resolution.action is InteractionAction.CLARIFY
    assert resolution.reason_code == "INTERACTION_EFFECT_AMBIGUOUS"
