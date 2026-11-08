"""Read, plan, local conflict, and mixed intent guards."""

from __future__ import annotations

from .evidence import (
    normalize_clause_for_guard,
)
from .guard_cross import parse_negative_restriction
from .guard_operational import (
    _all_marker_occurrences,
    _plain_effect_occurrence,
    _positive_effect,
    _starts_lexeme,
    analyze_operational_clause,
    strip_one_request_prefix,
)
from .guard_types import (
    LocalConflictClassification,
    MixedIntentClassification,
    OperationalClassification,
    PlanClassification,
    ReadClassification,
    _is_contextual_complement,
)
from .lexicon import (
    LOCAL_CONFLICT_SEPARATORS,
    MIXED_INTENT_MARKERS,
    PLAN_LEXEMES,
    READ_LEXEMES,
)


def _start_read(value: str) -> tuple[str, str] | None:
    return _starts_lexeme(value, tuple(sorted(READ_LEXEMES, key=len, reverse=True)))


def classify_read_request(clause: str) -> ReadClassification:
    normalized = normalize_clause_for_guard(clause)
    stripped, _ = strip_one_request_prefix(normalized)
    operational = analyze_operational_clause(normalized)
    if operational.classification in {
        OperationalClassification.DIRECT,
        OperationalClassification.NEGATED,
        OperationalClassification.HYPOTHETICAL,
        OperationalClassification.CONTEXTUAL,
    }:
        return ReadClassification.OPERATIONAL
    if _start_plan(stripped) is not None:
        return ReadClassification.PROPOSAL
    read = _start_read(stripped)
    if read is None:
        if _plain_effect_occurrence(normalized):
            return ReadClassification.META
        return ReadClassification.UNKNOWN
    _, complement = read
    if _is_contextual_complement(complement):
        return ReadClassification.CONTEXTUAL
    return ReadClassification.DIRECT_READ if complement.strip() else ReadClassification.UNKNOWN


class DirectReadRequestGuard:
    @staticmethod
    def classify(clause: str) -> ReadClassification:
        return classify_read_request(clause)


def _start_plan(value: str) -> tuple[str, str] | None:
    return _starts_lexeme(value, tuple(sorted(PLAN_LEXEMES, key=len, reverse=True)))


def classify_plan_request(clause: str) -> PlanClassification:
    normalized = normalize_clause_for_guard(clause)
    stripped, _ = strip_one_request_prefix(normalized)
    operational = analyze_operational_clause(normalized)
    if operational.classification in {
        OperationalClassification.DIRECT,
        OperationalClassification.NEGATED,
        OperationalClassification.HYPOTHETICAL,
        OperationalClassification.CONTEXTUAL,
    }:
        return PlanClassification.OPERATIONAL
    found = _start_plan(stripped)
    if found is None:
        if _plain_effect_occurrence(normalized):
            return PlanClassification.META
        return PlanClassification.UNKNOWN
    _, complement = found
    if not complement.strip() or _is_contextual_complement(complement):
        return PlanClassification.CONTEXTUAL
    return PlanClassification.DIRECT_PLAN


class DirectPlanRequestGuard:
    @staticmethod
    def classify(clause: str) -> PlanClassification:
        return classify_plan_request(clause)


def local_effect_conflict(clause: str) -> LocalConflictClassification:
    """Scan every closed separator tail through the negative parser.

    A later same-family restriction remains a veto after unrelated families.
    The scan is exhausted only after all candidate tails are examined.
    """
    normalized = normalize_clause_for_guard(clause)
    primary = analyze_operational_clause(normalized)
    if primary.classification is not OperationalClassification.DIRECT or primary.family is None:
        return LocalConflictClassification.CLEAR
    remainder_start = normalized.find(primary.predicate or "") + len(primary.predicate or "")
    remainder = normalized[remainder_start:]
    for _separator_start, separator_end in _all_marker_occurrences(remainder, LOCAL_CONFLICT_SEPARATORS):
        restriction = parse_negative_restriction(remainder[separator_end:].strip())
        if restriction is not None and restriction.family in {"ALL", primary.family}:
            return LocalConflictClassification.CONFLICT
    return LocalConflictClassification.CLEAR


class LocalEffectConflictGuard:
    @staticmethod
    def classify(clause: str) -> LocalConflictClassification:
        return local_effect_conflict(clause)


def mixed_intent_tail(clause: str) -> MixedIntentClassification:
    normalized = normalize_clause_for_guard(clause)
    stripped, _ = strip_one_request_prefix(normalized)
    read = classify_read_request(normalized)
    plan = classify_plan_request(normalized)
    if read is not ReadClassification.DIRECT_READ and plan is not PlanClassification.DIRECT_PLAN:
        return MixedIntentClassification.CLEAR
    found = _start_read(stripped) or _start_plan(stripped)
    if found is None:
        return MixedIntentClassification.CLEAR
    remainder = found[1]
    for _marker_start, marker_end in _all_marker_occurrences(remainder, MIXED_INTENT_MARKERS):
        tail = remainder[marker_end:]
        if _positive_effect(tail) is not None:
            return MixedIntentClassification.MIXED_EFFECT
    return MixedIntentClassification.CLEAR


class MixedIntentTailGuard:
    @staticmethod
    def classify(clause: str) -> MixedIntentClassification:
        return mixed_intent_tail(clause)
