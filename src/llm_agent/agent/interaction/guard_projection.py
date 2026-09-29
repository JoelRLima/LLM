"""Deterministic guard-result to interaction-resolution projection."""

from __future__ import annotations

from typing import Any

from .continue_intent import ResumeClassification
from .errors import (
    INTERACTION_CONFLICT,
    INTERACTION_CONTEXT_GROUNDING_REQUIRED,
    INTERACTION_CONTINUATION_AMBIGUOUS,
    INTERACTION_EFFECT_AMBIGUOUS,
    INTERACTION_INTENT_AMBIGUOUS,
    INTERACTION_RESUME_OVERRIDE_FORBIDDEN,
    INTERACTION_TASK_INTENT_REQUIRED,
)
from .guards import (
    CrossClauseRelation,
    LocalConflictClassification,
    MixedIntentClassification,
    OperationalClassification,
    PlanClassification,
    ReadClassification,
    TargetProof,
)
from .types import (
    InteractionAction,
    InteractionAmbiguity,
    InteractionBoundary,
    InteractionProvenance,
    InteractionResolution,
)


def project_guard_result(
    result: Any,
    *,
    boundary: InteractionBoundary,
    task_respond: bool = False,
) -> InteractionResolution:
    """Project one deterministic guard result to CLARIFY."""

    from .admission import _resolution

    if result in {ReadClassification.CONTEXTUAL, PlanClassification.CONTEXTUAL, OperationalClassification.CONTEXTUAL, ResumeClassification.CONTEXTUAL}:
        return _resolution(
            action=InteractionAction.CLARIFY,
            boundary=boundary,
            directive=None,
            profile=None,
            provenance=InteractionProvenance.DETERMINISTIC,
            ambiguity=InteractionAmbiguity.GROUNDING if result is not ResumeClassification.CONTEXTUAL else InteractionAmbiguity.CONTINUATION,
            reason_code=INTERACTION_CONTEXT_GROUNDING_REQUIRED if result is not ResumeClassification.CONTEXTUAL else INTERACTION_CONTINUATION_AMBIGUOUS,
        )
    if result in {OperationalClassification.CONFLICT, LocalConflictClassification.CONFLICT, CrossClauseRelation.FAMILY_CONFLICT, CrossClauseRelation.SAME_TARGET_CONFLICT, CrossClauseRelation.GLOBAL_CONFLICT, CrossClauseRelation.UNKNOWN_RELATION_CONFLICT, MixedIntentClassification.MIXED_EFFECT, ReadClassification.OPERATIONAL, ReadClassification.PROPOSAL, PlanClassification.OPERATIONAL}:
        return _resolution(
            action=InteractionAction.CLARIFY,
            boundary=boundary,
            directive=None,
            profile=None,
            provenance=InteractionProvenance.DETERMINISTIC,
            ambiguity=InteractionAmbiguity.CONFLICT,
            reason_code=INTERACTION_CONFLICT,
        )
    if result is ResumeClassification.OVERRIDE:
        return _resolution(
            action=InteractionAction.CLARIFY,
            boundary=boundary,
            directive=None,
            profile=None,
            provenance=InteractionProvenance.DETERMINISTIC,
            ambiguity=InteractionAmbiguity.CONFLICT,
            reason_code=INTERACTION_RESUME_OVERRIDE_FORBIDDEN,
        )
    if result in {
        OperationalClassification.NEGATED,
        OperationalClassification.HYPOTHETICAL,
        OperationalClassification.QUOTED,
        OperationalClassification.META,
        OperationalClassification.UNKNOWN,
        ResumeClassification.NEGATED,
        ResumeClassification.HYPOTHETICAL,
        ResumeClassification.META,
        ResumeClassification.UNKNOWN,
        TargetProof.UNPROVEN,
    }:
        return _resolution(
            action=InteractionAction.CLARIFY,
            boundary=boundary,
            directive=None,
            profile=None,
            provenance=InteractionProvenance.DETERMINISTIC,
            ambiguity=InteractionAmbiguity.CONTINUATION if isinstance(result, ResumeClassification) else InteractionAmbiguity.EFFECT,
            reason_code=INTERACTION_CONTINUATION_AMBIGUOUS if isinstance(result, ResumeClassification) else INTERACTION_EFFECT_AMBIGUOUS,
        )
    return _resolution(
        action=InteractionAction.CLARIFY,
        boundary=boundary,
        directive=None,
        profile=None,
        provenance=InteractionProvenance.DETERMINISTIC,
        ambiguity=InteractionAmbiguity.NONE,
        reason_code=INTERACTION_TASK_INTENT_REQUIRED if task_respond else INTERACTION_INTENT_AMBIGUOUS,
    )
