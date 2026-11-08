"""Negative restrictions and cross clause conflict analysis."""

from __future__ import annotations

from .evidence import (
    evidence_is_plain_exact,
    plain_occurrences,
    scan_clause_spans,
    strip_target_surrounding_punctuation,
)
from .guard_operational import _negative_effect, analyze_operational_clause, strip_one_request_prefix
from .guard_targets import _target_fragment, normalize_target_anchor_identity
from .guard_types import (
    CrossClauseRelation,
    NegativeRestriction,
    OperationalAnalysis,
    OperationalClassification,
    PlanClassification,
    ReadClassification,
    TargetAnchorIdentity,
    TargetProof,
)
from .lexicon import (
    BROAD_TARGET_QUANTIFIERS,
    GLOBAL_FAMILY_CORES,
    GLOBAL_RESTRICTIONS,
)


def _global_restriction(value: str) -> bool:
    if value in GLOBAL_RESTRICTIONS:
        return True
    for _family, cores in GLOBAL_FAMILY_CORES.items():
        for core in cores:
            if value == core or value.startswith(core + " "):
                return True
    return False


def parse_negative_restriction(clause: str) -> NegativeRestriction | None:
    normalized, _ = strip_one_request_prefix(clause)
    if _global_restriction(normalized):
        for family, cores in GLOBAL_FAMILY_CORES.items():
            for core in cores:
                if normalized == core or normalized.startswith(core + " "):
                    return NegativeRestriction(family, "FAMILY_ALL", normalized[len(core) :].strip())
        return NegativeRestriction("ALL", "FAMILY_ALL", normalized)
    negative = _negative_effect(normalized)
    if negative is None:
        return None
    family, predicate_or_scope, complement = negative
    if predicate_or_scope in {"FAMILY_ALL", "TARGET_SCOPED"}:
        return NegativeRestriction(family, predicate_or_scope, complement)
    return NegativeRestriction(family, "TARGET_SCOPED", complement)


def _target_for_relation(analysis: OperationalAnalysis | NegativeRestriction) -> TargetAnchorIdentity:
    value: str | None
    if isinstance(analysis, NegativeRestriction):
        value = analysis.complement
    else:
        value = _target_fragment(analysis)
        if value is None:
            return TargetAnchorIdentity("AMBIGUOUS", None, True)
    return normalize_target_anchor_identity(value)


def cross_clause_effect_conflict(subject: str) -> CrossClauseRelation:
    positives, negatives = _collect_clause_claims(subject)
    if not positives or not negatives:
        return CrossClauseRelation.CLEAR
    if any(restriction.family == "ALL" and restriction.scope == "FAMILY_ALL" for restriction in negatives):
        return CrossClauseRelation.GLOBAL_CONFLICT

    relevant = _relevant_restrictions(negatives, positives)
    if not relevant:
        return CrossClauseRelation.CLEAR
    return _compare_relevant_restrictions(relevant, positives)


def _collect_clause_claims(
    subject: str,
) -> tuple[list[OperationalAnalysis], list[NegativeRestriction]]:
    positives: list[OperationalAnalysis] = []
    negatives: list[NegativeRestriction] = []
    for clause in scan_clause_spans(subject):
        analysis = analyze_operational_clause(clause.text)
        if analysis.classification is OperationalClassification.DIRECT and analysis.family is not None:
            positives.append(analysis)
        restriction = parse_negative_restriction(clause.text)
        if restriction is not None:
            negatives.append(restriction)
    return positives, negatives


def _relevant_restrictions(
    negatives: list[NegativeRestriction], positives: list[OperationalAnalysis]
) -> list[NegativeRestriction]:
    return [
        restriction
        for restriction in negatives
        if restriction.family == "ALL" or any(positive.family == restriction.family for positive in positives)
    ]


def _compare_relevant_restrictions(
    relevant: list[NegativeRestriction], positives: list[OperationalAnalysis]
) -> CrossClauseRelation:
    saw_independent = False
    for restriction in relevant:
        matching_positives = [
            positive for positive in positives if restriction.family == "ALL" or positive.family == restriction.family
        ]
        for positive in matching_positives:
            relation = _compare_claim_pair(restriction, positive)
            if relation is CrossClauseRelation.INDEPENDENT:
                saw_independent = True
            elif relation is not CrossClauseRelation.CLEAR:
                return relation
    return CrossClauseRelation.INDEPENDENT if saw_independent else CrossClauseRelation.CLEAR


def _compare_claim_pair(restriction: NegativeRestriction, positive: OperationalAnalysis) -> CrossClauseRelation:
    if restriction.scope == "FAMILY_ALL":
        return CrossClauseRelation.FAMILY_CONFLICT
    negative_identity = _target_for_relation(restriction)
    positive_identity = _target_for_relation(positive)
    if _same_resolved_identity(negative_identity, positive_identity):
        return CrossClauseRelation.SAME_TARGET_CONFLICT
    if _identity_relation_is_unknown(negative_identity, positive_identity):
        return CrossClauseRelation.UNKNOWN_RELATION_CONFLICT
    if _identities_are_independent(restriction, negative_identity, positive_identity):
        return CrossClauseRelation.INDEPENDENT
    return CrossClauseRelation.UNKNOWN_RELATION_CONFLICT


def _same_resolved_identity(left: TargetAnchorIdentity, right: TargetAnchorIdentity) -> bool:
    return (
        left.canonical is not None
        and left.canonical == right.canonical
        and not left.identity_unresolved
        and not right.identity_unresolved
    )


def _identity_relation_is_unknown(left: TargetAnchorIdentity, right: TargetAnchorIdentity) -> bool:
    return (
        left.identity_unresolved
        or right.identity_unresolved
        or left.identity_class in {"URL", "EMAIL"}
        or right.identity_class in {"URL", "EMAIL"}
    )


def _identities_are_independent(
    restriction: NegativeRestriction,
    negative: TargetAnchorIdentity,
    positive: TargetAnchorIdentity,
) -> bool:
    broad = {
        strip_target_surrounding_punctuation(token) for token in restriction.complement.split()
    } & BROAD_TARGET_QUANTIFIERS
    bounded = {"LOCAL_PATH", "IDENTIFIER"}
    return (
        not broad
        and negative.canonical is not None
        and positive.canonical is not None
        and negative.identity_class in bounded
        and positive.identity_class in bounded
    )


class CrossClauseEffectConflictGuard:
    @staticmethod
    def classify(subject: str) -> CrossClauseRelation:
        return cross_clause_effect_conflict(subject)


def evidence_is_current_plain(subject: str, evidence: str) -> bool:
    return bool(evidence_is_plain_exact(subject, evidence))


def evidence_is_within_one_clause(subject: str, evidence: str) -> bool:
    for start, end in plain_occurrences(subject, evidence, limit=32):
        for clause in scan_clause_spans(subject):
            if clause.start <= start and end <= clause.end:
                return True
    return False


DirectOperationalResult = OperationalClassification
DirectReadResult = ReadClassification
DirectPlanResult = PlanClassification
TargetGuardResult = TargetProof
