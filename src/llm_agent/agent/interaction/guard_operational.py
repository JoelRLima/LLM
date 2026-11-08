"""Speech act classification for operational requests."""

from __future__ import annotations

from .evidence import (
    SpanKind,
    normalize_clause_for_guard,
    plain_occurrences,
    scan_spans,
)
from .guard_types import (
    OperationalAnalysis,
    OperationalClassification,
    _is_contextual_complement,
)
from .lexicon import (
    ALL_EFFECT_LEXEMES,
    CONDITIONAL_PREFIXES,
    EFFECT_LEXICON,
    NEGATION_PREFIXES,
    NEGATIVE_INFINITIVE_FORMS,
    REQUEST_PREFIXES,
)

_CONTEXTUAL_OPERATION_FORMS = frozenset(
    {
        "faz isso",
        "faça isso",
        "faca isso",
        "do that",
        "do this",
    }
)


def strip_one_request_prefix(clause: str) -> tuple[str, str | None]:
    value = normalize_clause_for_guard(clause)
    for prefix in REQUEST_PREFIXES:
        boundary = prefix + " "
        if value.startswith(boundary):
            return value[len(boundary) :], prefix
    return value, None


def _starts_lexeme(value: str, lexemes: tuple[str, ...]) -> tuple[str, str] | None:
    for lexeme in sorted(lexemes, key=len, reverse=True):
        if value.startswith(lexeme + " "):
            return lexeme, value[len(lexeme) + 1 :]
    return None


def _positive_effect(value: str, *, include_cross_only: bool = False) -> tuple[str, str, str] | None:
    for family, lexemes in EFFECT_LEXICON.items():
        available = lexemes
        if include_cross_only and family == "WRITE":
            available = tuple(
                sorted(
                    set(lexemes) | {"alter", "mexa", "toque", "touch"},
                    key=len,
                    reverse=True,
                )
            )
        found = _starts_lexeme(value, available)
        if found is not None:
            predicate, complement = found
            if complement:
                return family, predicate, complement
    return None


def _positive_effect_signal(value: str, *, include_cross_only: bool = False) -> tuple[str, str] | None:
    for family, lexemes in EFFECT_LEXICON.items():
        available = lexemes
        if include_cross_only and family == "WRITE":
            available = tuple(
                sorted(
                    set(lexemes) | {"alter", "mexa", "toque", "touch"},
                    key=len,
                    reverse=True,
                )
            )
        found = _starts_lexeme(value + " ", available)
        if found is not None:
            return family, found[0]
    return None


def _all_marker_occurrences(value: str, markers: tuple[str, ...]) -> list[tuple[int, int]]:
    occurrences: list[tuple[int, int]] = []
    for marker in set(markers):
        needle = marker + " "
        cursor = 0
        while (index := value.find(needle, cursor)) >= 0:
            if marker and marker[0].isalnum() and index > 0 and not value[index - 1].isspace():
                cursor = index + 1
                continue
            occurrences.append((index, index + len(needle)))
            cursor = index + 1
    return sorted(occurrences, key=lambda item: (item[0], -(item[1] - item[0])))


def _negative_effect(value: str) -> tuple[str, str, str] | None:
    for marker in sorted(NEGATION_PREFIXES, key=len, reverse=True):
        prefix = marker + " "
        if value.startswith(prefix):
            positive = _positive_effect(value[len(prefix) :], include_cross_only=True)
            if positive is not None:
                family, predicate, complement = positive
                return family, predicate, complement
    for family, scopes in NEGATIVE_INFINITIVE_FORMS.items():
        for scope, forms in scopes.items():
            for form in sorted(forms, key=len, reverse=True):
                suffix = value.removeprefix(form)
                if (scope == "FAMILY_ALL" and not suffix) or (suffix.startswith(" ") and suffix[1:]):
                    return family, scope, suffix.strip()
    return None


def _conditional_prefix(value: str) -> bool:
    for prefix in sorted(CONDITIONAL_PREFIXES, key=len, reverse=True):
        if value == prefix or value.startswith(prefix + " "):
            return True
    return False


def _plain_effect_occurrence(value: str) -> bool:
    for lexeme in ALL_EFFECT_LEXEMES:
        if plain_occurrences(value, lexeme, limit=1):
            return True
    return False


def _plain_char_positions(value: str) -> set[int]:
    positions: set[int] = set()
    for span in scan_spans(value):
        if span.kind is SpanKind.PLAIN:
            positions.update(range(span.start, span.end))
    return positions


def _standalone_plain_conditional(value: str, after: int) -> bool:
    positions = _plain_char_positions(value)
    for token in ("se", "caso", "if"):
        cursor = after
        while True:
            found = value.find(token, cursor)
            if found < 0:
                break
            before_ok = found == 0 or value[found - 1].isspace() or value[found - 1] in ",;.!?"
            end = found + len(token)
            after_ok = end == len(value) or value[end].isspace() or value[end] in ",;.!?"
            if before_ok and after_ok and all(index in positions for index in range(found, end)):
                return True
            cursor = found + 1
    return False


def analyze_operational_clause(clause: str) -> OperationalAnalysis:
    normalized = normalize_clause_for_guard(clause)
    if not normalized:
        return OperationalAnalysis(OperationalClassification.UNKNOWN)
    stripped, _ = strip_one_request_prefix(normalized)
    prefix_offset = len(normalized) - len(stripped)

    if stripped in _CONTEXTUAL_OPERATION_FORMS:
        return OperationalAnalysis(OperationalClassification.CONTEXTUAL)
    if _conditional_prefix(stripped) and _plain_effect_occurrence(stripped):
        return OperationalAnalysis(OperationalClassification.HYPOTHETICAL)
    negative = _negative_effect(stripped)
    if negative is not None:
        family, predicate, complement = negative
        return OperationalAnalysis(OperationalClassification.NEGATED, family, predicate, complement, prefix_offset)
    positive = _positive_effect(stripped)
    if positive is not None:
        return _classify_positive(normalized, positive, prefix_offset)
    return _classify_nonleading_effect(normalized, stripped)


def _classify_positive(normalized: str, positive: tuple[str, str, str], prefix_offset: int) -> OperationalAnalysis:
    family, predicate, complement = positive
    predicate_end = prefix_offset + len(predicate)
    if _standalone_plain_conditional(normalized, predicate_end):
        return OperationalAnalysis(OperationalClassification.HYPOTHETICAL, family, predicate, complement, predicate_end)
    classification = (
        OperationalClassification.CONTEXTUAL
        if _is_contextual_complement(complement)
        else OperationalClassification.DIRECT
    )
    return OperationalAnalysis(classification, family, predicate, complement, prefix_offset)


def _classify_nonleading_effect(normalized: str, stripped: str) -> OperationalAnalysis:
    del stripped
    if _plain_effect_occurrence(normalized):
        meta_markers = (
            "instrução",
            "instrucao",
            "instruction",
            "phrase",
            "frase",
            "appears",
            "aparece",
            "example",
            "exemplo",
            "explicação",
            "explicacao",
            "explanation",
            "mentioned",
            "mencionada",
        )
        if any(marker in normalized for marker in meta_markers):
            return OperationalAnalysis(OperationalClassification.META)
    for span in scan_spans(normalized):
        if span.kind is not SpanKind.PLAIN and _plain_effect_occurrence(span.text):
            return OperationalAnalysis(OperationalClassification.QUOTED)
    return OperationalAnalysis(OperationalClassification.UNKNOWN)


def classify_operational_request(clause: str) -> OperationalClassification:
    return analyze_operational_clause(clause).classification


class DirectOperationalRequestGuard:
    @staticmethod
    def classify(clause: str) -> OperationalClassification:
        return classify_operational_request(clause)

    @staticmethod
    def analyze(clause: str) -> OperationalAnalysis:
        return analyze_operational_clause(clause)
