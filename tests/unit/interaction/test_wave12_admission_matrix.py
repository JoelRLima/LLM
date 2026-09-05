from __future__ import annotations

import pytest

from agent.interaction.admission import admit_interaction, project_guard_result
from agent.interaction.continue_intent import ResumeClassification
from agent.interaction.guards import (
    CrossClauseRelation,
    LocalConflictClassification,
    MixedIntentClassification,
    ReadClassification,
)
from agent.interaction.types import (
    ActionGrounding,
    InteractionAction,
    InteractionAmbiguity,
    InteractionBoundary,
    InteractionModelDecision,
)
from agent.interfaces.task_directives import parse_task_request
from agent.runtime.task_directives import DeliberationProfile, TaskDirective


def candidate(**overrides: object) -> InteractionModelDecision:
    value: dict[str, object] = {
        "action": InteractionAction.RESPOND,
        "directive": None,
        "ambiguity": InteractionAmbiguity.NONE,
        "grounding": ActionGrounding.NONE,
        "operation_requested": False,
        "proposal_only": False,
        "resume_requested": False,
        "evidence": "",
    }
    value.update(overrides)
    return InteractionModelDecision(**value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("subject", "action", "directive", "evidence"),
    [
        ("What is an AST?", InteractionAction.RESPOND, None, ""),
        ("Analyze parser.py", InteractionAction.RUN, TaskDirective.READ, "Analyze parser.py"),
        ("Propose a plan for parser.py", InteractionAction.RUN, TaskDirective.PLAN, "Propose a plan for parser.py"),
        ("Delete parser.py", InteractionAction.RUN, TaskDirective.DO, "Delete parser.py"),
        ("Resume the previous task", InteractionAction.CONTINUE, None, "Resume the previous task"),
    ],
)
def test_natural_closed_actions_admit_through_one_matrix(
    subject: str,
    action: InteractionAction,
    directive: TaskDirective | None,
    evidence: str,
) -> None:
    flags = {
        "operation_requested": directive is TaskDirective.DO,
        "proposal_only": directive is TaskDirective.PLAN,
        "resume_requested": action is InteractionAction.CONTINUE,
        "grounding": ActionGrounding.CURRENT_TURN if action in {InteractionAction.RUN, InteractionAction.CONTINUE} else ActionGrounding.NONE,
        "evidence": evidence,
    }
    result = admit_interaction(
        boundary=InteractionBoundary.NATURAL,
        visible_user_text=subject,
        subject=subject,
        model_decision=candidate(action=action, directive=directive, **flags),
    )
    assert result.action is action
    assert result.directive is directive


def test_task_semantic_respond_is_safe_read_only_for_grounded_read() -> None:
    subject = "What is an AST?"
    result = admit_interaction(
        boundary=InteractionBoundary.TASK,
        visible_user_text=subject,
        subject=subject,
        model_decision=candidate(),
    )
    assert result.action is InteractionAction.RUN
    assert result.directive is TaskDirective.READ


def test_task_casual_semantic_respond_requires_task_intent() -> None:
    subject = "Oi"
    result = admit_interaction(
        boundary=InteractionBoundary.TASK,
        visible_user_text=subject,
        subject=subject,
        model_decision=candidate(),
    )
    assert result.action is InteractionAction.CLARIFY
    assert result.reason_code == "INTERACTION_TASK_INTENT_REQUIRED"


def test_explicit_task_directive_bypasses_resolver_and_preserves_profile() -> None:
    parsed = parse_task_request("/read /smart Analyze parser.py")
    result = admit_interaction(
        boundary=InteractionBoundary.TASK,
        visible_user_text="/agent /read /smart Analyze parser.py",
        subject=parsed.subject or "",
        parsed_task=parsed,
    )
    assert result.action is InteractionAction.RUN
    assert result.directive is TaskDirective.READ
    assert result.deliberation_profile is DeliberationProfile.SMART
    assert result.provenance.value == "explicit"


def test_profile_only_is_model_resolved_and_continue_override_is_refused() -> None:
    parsed = parse_task_request("/smart Analyze parser.py")
    read = admit_interaction(
        boundary=InteractionBoundary.TASK,
        visible_user_text="/smart Analyze parser.py",
        subject=parsed.subject or "",
        parsed_task=parsed,
        model_decision=candidate(
            action=InteractionAction.RUN,
            directive=TaskDirective.READ,
            grounding=ActionGrounding.CURRENT_TURN,
            evidence="Analyze parser.py",
        ),
    )
    assert read.deliberation_profile is DeliberationProfile.SMART
    resumed = admit_interaction(
        boundary=InteractionBoundary.TASK,
        visible_user_text="/smart Resume the previous task",
        subject="Resume the previous task",
        parsed_task=parsed,
        model_decision=candidate(
            action=InteractionAction.CONTINUE,
            grounding=ActionGrounding.CURRENT_TURN,
            resume_requested=True,
            evidence="Resume the previous task",
        ),
    )
    assert resumed.action is InteractionAction.CLARIFY
    assert resumed.reason_code == "INTERACTION_RESUME_OVERRIDE_FORBIDDEN"


def test_evidence_mismatch_is_total_pre_guard_projection() -> None:
    with pytest.raises(ValueError, match="INTERACTION_EVIDENCE_MISMATCH"):
        admit_interaction(
            boundary=InteractionBoundary.NATURAL,
            visible_user_text="Delete parser.py",
            subject="Delete parser.py",
            model_decision=candidate(
                action=InteractionAction.RUN,
                directive=TaskDirective.DO,
                grounding=ActionGrounding.CURRENT_TURN,
                operation_requested=True,
                evidence="other.py",
            ),
        )


@pytest.mark.parametrize(
    "guard_result",
    [
        ReadClassification.CONTEXTUAL,
        LocalConflictClassification.CONFLICT,
        CrossClauseRelation.UNKNOWN_RELATION_CONFLICT,
        MixedIntentClassification.MIXED_EFFECT,
        ResumeClassification.OVERRIDE,
    ],
)
def test_all_guard_results_have_one_clarify_projection(guard_result: object) -> None:
    result = project_guard_result(guard_result, boundary=InteractionBoundary.NATURAL)
    assert result.action is InteractionAction.CLARIFY
    assert result.provenance.value == "deterministic"
