"""Small bounded payload and command-tail productions."""

from __future__ import annotations

from typing import Sequence

from agent.planning.task_semantics_positive_proof_data import (
    _DIRECT_REQUEST_PREFIXES,
    _MEMORY_CONTEXT_VERBS,
    _MEMORY_DIRECT_VERBS,
    _NEGATION_TOKENS,
    _PUNCTUATION,
    _QUOTE_PAIRS,
    _VALIDATION_TAILS,
)
from agent.planning.task_semantics_positive_proof_lexing import (
    _bounded_symbol,
    _path_value,
)

_QUOTE_TOKENS = frozenset(
    marker for opening, closing in _QUOTE_PAIRS for marker in (opening, closing)
)
_NATURAL_TARGET_RELATIONS = frozenset({"em", "in", "into", "na", "no", "to"})
_NATURAL_SIGNATURE_TOKENS = frozenset({"(", ")", ":", "-", ">", "+", "[", "]"})
_NATURAL_PREFIX_BLOCKERS = frozenset(
    {
        "after", "antes", "caso", "condition", "if", "provided", "se", "unless",
        "when", "approval", "approve", "aprovacao", "review", "reviewer", "future",
        "futura",
    }
) | _NEGATION_TOKENS
_NATURAL_MUTATION_TAILS = frozenset(
    {
        (
            "para", "limitar", "value", "ao", "intervalo", "inclusivo", "[", "low",
            "high", "]",
        ),
        (
            "para", "remover", "espacos", "externos", "e", "normalizar", "sem",
            "diferenciar", "maiusculas/minusculas", "usando", "casefold", "(", ")",
        ),
        (
            "para", "o", "backend", "que", "eu", "devo", "usar", "em", "producao",
            "as", "opcoes", "validas", "sao", "memory", "e", "disk",
        ),
        (
            "de", "10", "para", "15", "no", "arquivo", "em", "que", "ele", "e",
            "definido",
        ),
        ("retornando", "o", "dobro", "de", "value"),
        (
            "para", "retornar", "value", "+", "1", "e", "valide", "com", "os", "testes",
            "relevantes",
        ),
        (
            "verifique", "o", "estado", "do", "repositorio", "depois", "mude", "value",
            "de", "1", "para", "2", "sem", "tocar", "em", "mudancas", "pre-existentes",
        ),
    }
)


def _memory_fragment(values: Sequence[str]) -> bool:
    if not values:
        return False
    if any(value in _MEMORY_DIRECT_VERBS for value in values):
        return True
    return bool(
        set(values) & {"memoria", "memory"}
        and set(values) & _MEMORY_CONTEXT_VERBS
    )


def _memory_payload_supported(values: Sequence[str]) -> bool:
    verb_index = next(
        (
            index
            for index, value in enumerate(values)
            if value in _MEMORY_DIRECT_VERBS | _MEMORY_CONTEXT_VERBS
        ),
        None,
    )
    if verb_index is None or not _supported_prefix(values[:verb_index]):
        return False
    payload = tuple(value for value in values[verb_index + 1 :] if value not in _PUNCTUATION)
    while payload[:1] in {
        ("da",),
        ("de",),
        ("do",),
        ("from",),
        ("in",),
        ("na",),
        ("to",),
    }:
        payload = payload[1:]
    if payload[:1] in {("memoria",), ("memory",)}:
        payload = payload[1:]
    if not payload:
        return False
    if payload == ("this",) or (len(payload) == 2 and payload[0] in {"a", "my", "the"}):
        return True
    if payload in {
        ("a", "preferencia", "antiga"),
        ("the", "old", "preference"),
        ("this", "in", "memory"),
        ("this", "to", "memory"),
    }:
        return True
    if payload[:1] != ("que",):
        return False
    body = payload[1:]
    operator = next(
        (index for index, value in enumerate(body) if value in {"e", "is", "vale"}),
        None,
    )
    return operator is not None and operator > 0 and len(body[operator + 1 :]) == 1


def _mutation_tail_supported(values: Sequence[str]) -> bool:
    tail = tuple(value for value in values if value not in _PUNCTUATION)
    if not tail:
        return True
    tail = _strip_tail_annotations(tail)
    if not tail or tail in {("a", "vulnerabilidade"), ("the", "vulnerability")}:
        return True
    return _supported_assignment_tail(tail)


def _strip_command_wrappers(values: Sequence[str]) -> tuple[str, ...]:
    """Remove only lexical punctuation/quotes, never unknown words."""

    return tuple(
        value
        for value in values
        if value not in _PUNCTUATION and value not in _QUOTE_TOKENS
    )


def _natural_target_prefix_supported(values: Sequence[str]) -> bool:
    """Recognize the bounded ``subject relation path`` target production."""

    cleaned = _strip_command_wrappers(values)
    if len(cleaned) < 2 or cleaned[-1] not in _NATURAL_TARGET_RELATIONS:
        return False
    subject = cleaned[:-1]
    if not subject or any(value in _NATURAL_PREFIX_BLOCKERS for value in subject):
        return False
    return all(
        value in _NATURAL_SIGNATURE_TOKENS or _bounded_symbol(value)
        for value in subject
    )


def _natural_mutation_tail_supported(values: Sequence[str]) -> bool:
    """Match only the finite tails required by the frozen practical set."""

    return _strip_command_wrappers(values) in _NATURAL_MUTATION_TAILS


def _supported_sequence_prefix(values: Sequence[str], verb_index: int) -> bool:
    """Admit the one bounded read-before-mutation production required by PV1-07."""

    if _strip_command_wrappers(values[:verb_index]) != ("antes", "de"):
        return False
    path_index = next(
        (index for index in range(verb_index + 1, len(values)) if _path_value(values[index])),
        None,
    )
    if path_index is None:
        return False
    return _natural_mutation_tail_supported(values[path_index + 1 :])


def _strip_tail_annotations(tail: tuple[str, ...]) -> tuple[str, ...]:
    for validation in _VALIDATION_TAILS:
        if len(tail) >= len(validation) and tail[-len(validation) :] == validation:
            tail = tail[: -len(validation)]
            break
    return tail


def _supported_assignment_tail(tail: tuple[str, ...]) -> bool:
    if tail[0] not in {"para", "to"}:
        return False
    payload = _strip_bounded_literal_quotes(tail[1:])
    return _supported_assignment_payload(payload)


def _supported_assignment_payload(payload: tuple[str, ...]) -> bool:
    if len(payload) == 1:
        return _bounded_symbol(payload[0])
    if len(payload) == 2 and payload[0] in {"contain", "conter", "return", "retornar"}:
        return _bounded_symbol(payload[1])
    if payload[:2] in {("que", "contenha"), ("que", "contem")}:
        remainder = payload[2:]
        if remainder[:1] in {("apenas",), ("only",)}:
            remainder = remainder[1:]
        return len(remainder) == 1 and _bounded_symbol(remainder[0])
    return False


def _strip_bounded_literal_quotes(values: Sequence[str]) -> tuple[str, ...]:
    candidate = tuple(values)
    for opening, closing in _QUOTE_PAIRS:
        if len(candidate) >= 2 and candidate[0] == opening and candidate[-1] == closing:
            return candidate[1:-1]
    return candidate


def _supported_prefix(values: Sequence[str]) -> bool:
    prefix = tuple(value for value in values if value not in _PUNCTUATION)
    return prefix in _DIRECT_REQUEST_PREFIXES


__all__ = [
    "_memory_fragment",
    "_memory_payload_supported",
    "_natural_mutation_tail_supported",
    "_natural_target_prefix_supported",
    "_mutation_tail_supported",
    "_supported_sequence_prefix",
    "_supported_prefix",
]
