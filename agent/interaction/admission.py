"""Exhaustive W12 admission matrix and centralized guard projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.interfaces.task_directives import ParsedTaskRequest, TaskRequestAction
from agent.runtime.task_directives import DeliberationProfile, TaskDirective, TaskRunDirective

from .continue_intent import DirectTaskResumeGuard, ResumeClassification
from .errors import (
    INTERACTION_CONFLICT,
    INTERACTION_CONTEXT_GROUNDING_REQUIRED,
    INTERACTION_CONTINUATION_AMBIGUOUS,
    INTERACTION_EFFECT_AMBIGUOUS,
    INTERACTION_EVIDENCE_MISMATCH,
    INTERACTION_INPUT_INVALID,
    INTERACTION_INTENT_AMBIGUOUS,
    INTERACTION_RESUME_OVERRIDE_FORBIDDEN,
    INTERACTION_TASK_INTENT_REQUIRED,
    InteractionAdmissionError,
)
from .guards import (
    CrossClauseEffectConflictGuard,
    CrossClauseRelation,
    DirectOperationalRequestGuard,
    DirectOperationalTargetGuard,
    DirectPlanRequestGuard,
    DirectReadRequestGuard,
    LocalConflictClassification,
    LocalEffectConflictGuard,
    MixedIntentClassification,
    MixedIntentTailGuard,
    OperationalClassification,
    PlanClassification,
    ReadClassification,
    TargetProof,
    evidence_is_current_plain,
    evidence_is_within_one_clause,
)
from .profile import select_fresh_profile
from .types import (
    InteractionAction,
    InteractionAmbiguity,
    InteractionBoundary,
    InteractionModelDecision,
    InteractionProvenance,
    InteractionResolution,
)


@dataclass(frozen=True, slots=True)
class AdmissionContext:
    boundary: InteractionBoundary
    visible_user_text: str
    subject: str
    parsed_task: ParsedTaskRequest | None = None
    model_decision: InteractionModelDecision | None = None


def _resolution(
    *,
    action: InteractionAction,
    boundary: InteractionBoundary,
    directive: TaskDirective | None,
    profile: DeliberationProfile | None,
    provenance: InteractionProvenance,
    ambiguity: InteractionAmbiguity = InteractionAmbiguity.NONE,
    subject: str | None = None,
    reason_code: str | None = None,
) -> InteractionResolution:
    return InteractionResolution(
        action=action,
        boundary=boundary,
        directive=directive,
        deliberation_profile=profile,
        provenance=provenance,
        ambiguity=ambiguity,
        subject=subject,
        reason_code=reason_code,
    )


def project_guard_result(
    result: Any,
    *,
    boundary: InteractionBoundary,
    task_respond: bool = False,
) -> InteractionResolution:
    """One deterministic guard-result-to-CLARIFY projection table (P18.8)."""

    if result in {ReadClassification.CONTEXTUAL, PlanClassification.CONTEXTUAL, OperationalClassification.CONTEXTUAL, ResumeClassification.CONTEXTUAL}:
        return _resolution(
            action=InteractionAction.CLARIFY,
            boundary=boundary,
            directive=None,
            profile=None,
            provenance=InteractionProvenance.DETERMINISTIC,
            ambiguity=InteractionAmbiguity.GROUNDING if result is not ResumeClassification.CONTEXTUAL else InteractionAmbiguity.CONTINUATION,
            reason_code=(INTERACTION_CONTEXT_GROUNDING_REQUIRED if result is not ResumeClassification.CONTEXTUAL else INTERACTION_CONTINUATION_AMBIGUOUS),
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
            ambiguity=(InteractionAmbiguity.CONTINUATION if isinstance(result, ResumeClassification) else InteractionAmbiguity.EFFECT),
            reason_code=(INTERACTION_CONTINUATION_AMBIGUOUS if isinstance(result, ResumeClassification) else INTERACTION_EFFECT_AMBIGUOUS),
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


def _require_evidence(subject: str, decision: InteractionModelDecision) -> None:
    evidence = decision.evidence
    if decision.action is InteractionAction.RESPOND:
        return
    if decision.action is InteractionAction.CLARIFY:
        # CLARIFY may cite quoted or meta text from the current subject.
        # PLAIN and clause containment belong only to task-bearing actions.
        # Exact substring matching keeps the cited evidence byte-faithful.
        if evidence and (len(evidence) > 512 or evidence not in subject):
            raise InteractionAdmissionError(INTERACTION_EVIDENCE_MISMATCH)
        return
    if not evidence or len(evidence) > 512 or not evidence_is_current_plain(subject, evidence):
        raise InteractionAdmissionError(INTERACTION_EVIDENCE_MISMATCH)
    if decision.action is InteractionAction.RUN and decision.directive is TaskDirective.DO:
        if not evidence_is_within_one_clause(subject, evidence):
            raise InteractionAdmissionError(INTERACTION_EVIDENCE_MISMATCH)


def _first_clause(subject: str) -> str:
    from .evidence import scan_clause_spans

    clauses = scan_clause_spans(subject)
    return clauses[0].text if clauses else subject


def _positive_proven_effect_clause(subject: str) -> bool:
    from .evidence import scan_clause_spans

    for clause in scan_clause_spans(subject):
        analysis = DirectOperationalRequestGuard.analyze(clause.text)
        if analysis.classification is OperationalClassification.DIRECT and DirectOperationalTargetGuard.classify(analysis) is TargetProof.PROVEN:
            return True
    return False


def _admit_read(
    context: AdmissionContext,
    *,
    provenance: InteractionProvenance,
    profile: DeliberationProfile,
) -> InteractionResolution:
    guard = DirectReadRequestGuard.classify(context.subject)
    if guard is ReadClassification.DIRECT_READ:
        if _positive_proven_effect_clause(context.subject):
            return project_guard_result(MixedIntentClassification.MIXED_EFFECT, boundary=context.boundary)
        if MixedIntentTailGuard.classify(context.subject) is MixedIntentClassification.MIXED_EFFECT:
            return project_guard_result(MixedIntentClassification.MIXED_EFFECT, boundary=context.boundary)
        return _resolution(
            action=InteractionAction.RUN,
            boundary=context.boundary,
            directive=TaskDirective.READ,
            profile=profile,
            provenance=provenance,
            subject=context.subject,
        )
    if guard is ReadClassification.CONTEXTUAL:
        return project_guard_result(guard, boundary=context.boundary)
    if guard in {ReadClassification.OPERATIONAL, ReadClassification.PROPOSAL}:
        return project_guard_result(guard, boundary=context.boundary)
    return _resolution(
        action=InteractionAction.CLARIFY,
        boundary=context.boundary,
        directive=None,
        profile=None,
        provenance=InteractionProvenance.DETERMINISTIC,
        reason_code=INTERACTION_TASK_INTENT_REQUIRED if context.boundary is InteractionBoundary.TASK else INTERACTION_INTENT_AMBIGUOUS,
    )


def _admit_plan(
    context: AdmissionContext,
    *,
    provenance: InteractionProvenance,
    profile: DeliberationProfile,
) -> InteractionResolution:
    guard = DirectPlanRequestGuard.classify(context.subject)
    if guard is PlanClassification.DIRECT_PLAN:
        if _positive_proven_effect_clause(context.subject) or MixedIntentTailGuard.classify(context.subject) is MixedIntentClassification.MIXED_EFFECT:
            return project_guard_result(MixedIntentClassification.MIXED_EFFECT, boundary=context.boundary)
        return _resolution(
            action=InteractionAction.RUN,
            boundary=context.boundary,
            directive=TaskDirective.PLAN,
            profile=profile,
            provenance=provenance,
            subject=context.subject,
        )
    if guard is PlanClassification.CONTEXTUAL:
        return project_guard_result(guard, boundary=context.boundary)
    if guard is PlanClassification.OPERATIONAL:
        return project_guard_result(guard, boundary=context.boundary)
    return _resolution(
        action=InteractionAction.CLARIFY,
        boundary=context.boundary,
        directive=None,
        profile=None,
        provenance=InteractionProvenance.DETERMINISTIC,
        reason_code=INTERACTION_INTENT_AMBIGUOUS,
    )


def _admit_do(
    context: AdmissionContext,
    *,
    provenance: InteractionProvenance,
    profile: DeliberationProfile,
) -> InteractionResolution:
    analysis = DirectOperationalRequestGuard.analyze(_first_clause(context.subject))
    guard = analysis.classification
    if guard is OperationalClassification.CONTEXTUAL:
        return project_guard_result(guard, boundary=context.boundary)
    if guard is not OperationalClassification.DIRECT:
        return project_guard_result(guard, boundary=context.boundary)
    target = DirectOperationalTargetGuard.classify(analysis)
    local = LocalEffectConflictGuard.classify(_first_clause(context.subject))
    cross = CrossClauseEffectConflictGuard.classify(context.subject)
    if (
        target is TargetProof.PROVEN
        and local is LocalConflictClassification.CLEAR
        and cross in {CrossClauseRelation.CLEAR, CrossClauseRelation.INDEPENDENT}
    ):
        return _resolution(
            action=InteractionAction.RUN,
            boundary=context.boundary,
            directive=TaskDirective.DO,
            profile=profile,
            provenance=provenance,
            subject=context.subject,
        )
    if local is LocalConflictClassification.CONFLICT or cross is not CrossClauseRelation.CLEAR:
        return project_guard_result(local if local is LocalConflictClassification.CONFLICT else cross, boundary=context.boundary)
    return project_guard_result(target, boundary=context.boundary)


def _admit_continue(context: AdmissionContext) -> InteractionResolution:
    guard = DirectTaskResumeGuard.classify(context.subject)
    if guard is ResumeClassification.DIRECT_RESUME:
        return _resolution(
            action=InteractionAction.CONTINUE,
            boundary=context.boundary,
            directive=None,
            profile=None,
            provenance=InteractionProvenance.MODEL_INFERRED,
        )
    return project_guard_result(guard, boundary=context.boundary)


def _admit_model_candidate(context: AdmissionContext, decision: InteractionModelDecision) -> InteractionResolution:
    _require_evidence(context.subject, decision)
    if decision.action is InteractionAction.CLARIFY:
        mapping = {
            InteractionAmbiguity.EFFECT: (InteractionAmbiguity.EFFECT, INTERACTION_EFFECT_AMBIGUOUS),
            InteractionAmbiguity.CONTINUATION: (InteractionAmbiguity.CONTINUATION, INTERACTION_CONTINUATION_AMBIGUOUS),
            InteractionAmbiguity.GROUNDING: (InteractionAmbiguity.GROUNDING, INTERACTION_CONTEXT_GROUNDING_REQUIRED),
            InteractionAmbiguity.CONFLICT: (InteractionAmbiguity.CONFLICT, INTERACTION_CONFLICT),
        }
        ambiguity, reason = mapping[decision.ambiguity]
        return _resolution(
            action=InteractionAction.CLARIFY,
            boundary=context.boundary,
            directive=None,
            profile=None,
            provenance=InteractionProvenance.DETERMINISTIC,
            ambiguity=ambiguity,
            reason_code=reason,
        )
    explicit_profile = context.parsed_task.profile_explicit if context.parsed_task is not None else False
    profile = select_fresh_profile(
        context.subject,
        directive=decision.directive,
        profile_explicit=explicit_profile,
        explicit_profile=(context.parsed_task.directive.deliberation_profile if context.parsed_task and context.parsed_task.directive else None),
    )
    if decision.action is InteractionAction.RESPOND:
        if context.boundary is InteractionBoundary.NATURAL:
            return _resolution(
                action=InteractionAction.RESPOND,
                boundary=context.boundary,
                directive=None,
                profile=profile,
                provenance=InteractionProvenance.MODEL_INFERRED,
            )
        return _admit_read(context, provenance=InteractionProvenance.DETERMINISTIC, profile=profile)
    if decision.action is InteractionAction.RUN:
        if decision.directive is TaskDirective.READ:
            return _admit_read(context, provenance=InteractionProvenance.MODEL_INFERRED, profile=profile)
        if decision.directive is TaskDirective.PLAN:
            return _admit_plan(context, provenance=InteractionProvenance.MODEL_INFERRED, profile=profile)
        if decision.directive is TaskDirective.DO:
            return _admit_do(context, provenance=InteractionProvenance.MODEL_INFERRED, profile=profile)
        raise InteractionAdmissionError(INTERACTION_INTENT_AMBIGUOUS)
    if decision.action is InteractionAction.CONTINUE:
        return _admit_continue(context)
    raise InteractionAdmissionError(INTERACTION_INTENT_AMBIGUOUS)


def admit_interaction(
    *,
    boundary: InteractionBoundary | str,
    visible_user_text: str,
    subject: str,
    parsed_task: ParsedTaskRequest | None = None,
    model_decision: InteractionModelDecision | None = None,
) -> InteractionResolution:
    context = AdmissionContext(
        boundary=InteractionBoundary(boundary),
        visible_user_text=visible_user_text,
        subject=subject,
        parsed_task=parsed_task,
        model_decision=model_decision,
    )
    if parsed_task is not None and parsed_task.action is TaskRequestAction.CONTINUE:
        return _resolution(
            action=InteractionAction.CONTINUE,
            boundary=InteractionBoundary.TASK,
            directive=None,
            profile=None,
            provenance=InteractionProvenance.EXPLICIT,
        )
    if parsed_task is not None and parsed_task.directive_explicit:
        task_directive = parsed_task.directive
        directive = task_directive.directive if task_directive is not None else None
        if directive not in {TaskDirective.READ, TaskDirective.PLAN, TaskDirective.DO}:
            raise InteractionAdmissionError(INTERACTION_INPUT_INVALID)
        if not isinstance(task_directive, TaskRunDirective):
            raise InteractionAdmissionError(INTERACTION_INPUT_INVALID)
        return _resolution(
            action=InteractionAction.RUN,
            boundary=InteractionBoundary.TASK,
            directive=directive,
            profile=task_directive.deliberation_profile,
            provenance=InteractionProvenance.EXPLICIT,
            subject=task_directive.subject,
        )
    if model_decision is None:
        raise InteractionAdmissionError(INTERACTION_INTENT_AMBIGUOUS)
    if parsed_task is not None and parsed_task.profile_explicit and model_decision.action is InteractionAction.CONTINUE:
        return _resolution(
            action=InteractionAction.CLARIFY,
            boundary=InteractionBoundary.TASK,
            directive=None,
            profile=None,
            provenance=InteractionProvenance.DETERMINISTIC,
            ambiguity=InteractionAmbiguity.CONTINUATION,
            reason_code=INTERACTION_RESUME_OVERRIDE_FORBIDDEN,
        )
    return _admit_model_candidate(context, model_decision)


admit = admit_interaction


__all__ = [
    "AdmissionContext",
    "admit",
    "admit_interaction",
    "project_guard_result",
]
