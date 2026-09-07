"""Semantic-claim admission projection after current-subject binding."""

from __future__ import annotations

from typing import Any

from agent.runtime.task_directives import TaskDirective

from .errors import (
    INTERACTION_CONFLICT,
    INTERACTION_CONTEXT_GROUNDING_REQUIRED,
    INTERACTION_EFFECT_AMBIGUOUS,
    INTERACTION_INTENT_AMBIGUOUS,
    InteractionAdmissionError,
)
from .profile import select_fresh_profile
from .types import (
    InteractionAction,
    InteractionAmbiguity,
    InteractionProvenance,
    InteractionResolution,
)


def admit_semantic_candidate(
    context: Any,
    decision: Any,
    claim: Any,
) -> InteractionResolution:
    """Project an already-bound semantic claim without lexical inference."""

    from agent.planning.intent_admission import IntentAdmissionError, admit_intent_claim

    from .admission import _resolution

    admitted_intent = None
    if claim.ambiguity != "none":
        ambiguity_map = {
            "effect": (InteractionAmbiguity.EFFECT, INTERACTION_EFFECT_AMBIGUOUS),
            "target": (InteractionAmbiguity.GROUNDING, INTERACTION_CONTEXT_GROUNDING_REQUIRED),
            "constraint": (InteractionAmbiguity.EFFECT, INTERACTION_EFFECT_AMBIGUOUS),
            "conflict": (InteractionAmbiguity.CONFLICT, INTERACTION_CONFLICT),
            "grounding": (InteractionAmbiguity.GROUNDING, INTERACTION_CONTEXT_GROUNDING_REQUIRED),
        }
        ambiguity, reason = ambiguity_map[claim.ambiguity]
        return _resolution(
            action=InteractionAction.CLARIFY,
            boundary=context.boundary,
            directive=None,
            profile=None,
            provenance=InteractionProvenance.DETERMINISTIC,
            ambiguity=ambiguity,
            reason_code=reason,
            intent_claim=claim,
        )
    if context.authority_envelope is not None:
        try:
            admitted_intent = admit_intent_claim(
                claim,
                context.authority_envelope,
                current_subject=context.subject,
            )
        except IntentAdmissionError as exc:
            if exc.reason_code in {
                "INTENT_READ_SCOPE_DENIED",
                "INTENT_WRITE_SCOPE_DENIED",
                "INTENT_LITERAL_TARGET_INVALID",
                "INTENT_AMBIGUOUS",
            }:
                raise InteractionAdmissionError(INTERACTION_CONTEXT_GROUNDING_REQUIRED) from exc
            raise InteractionAdmissionError(INTERACTION_INTENT_AMBIGUOUS) from exc
    # The outer routing flag is advisory, but a proposal-only claim is a
    # stronger narrowing constraint.  The unsafe widening direction
    # (outer proposal_only with a normal semantic claim) is fail-closed.
    claim_proposal_only = claim.operation == "plan" or any(
        item.kind == "proposal_only" for item in claim.constraints
    )
    semantic_proposal_only = (
        admitted_intent.proposal_only
        if admitted_intent is not None
        else claim_proposal_only
    )
    if decision.proposal_only and not semantic_proposal_only:
        raise InteractionAdmissionError(INTERACTION_INTENT_AMBIGUOUS)
    if decision.action is not InteractionAction.RUN or decision.directive is None:
        raise InteractionAdmissionError(INTERACTION_INTENT_AMBIGUOUS)
    expected = {
        TaskDirective.READ: "read",
        TaskDirective.PLAN: "plan",
        TaskDirective.DO: "do",
    }.get(decision.directive)
    if expected != claim.operation:
        raise InteractionAdmissionError(INTERACTION_INTENT_AMBIGUOUS)
    explicit_profile = context.parsed_task.profile_explicit if context.parsed_task is not None else False
    profile = select_fresh_profile(
        context.subject,
        directive=decision.directive,
        profile_explicit=explicit_profile,
        explicit_profile=(
            context.parsed_task.directive.deliberation_profile
            if context.parsed_task and context.parsed_task.directive
            else None
        ),
    )
    return _resolution(
        action=InteractionAction.RUN,
        boundary=context.boundary,
        directive=decision.directive,
        profile=profile,
        provenance=InteractionProvenance.MODEL_INFERRED,
        subject=context.subject,
        intent_claim=claim,
        admitted_intent=admitted_intent,
    )
