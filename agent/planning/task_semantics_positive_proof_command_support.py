"""Small bounded payload and command-tail productions."""

from __future__ import annotations

from typing import Sequence

from agent.planning.task_semantics_positive_proof_data import (
    _DESTINATION_RELATIONS,
    _DIRECT_REQUEST_PREFIXES,
    _MEMORY_CONTEXT_VERBS,
    _MEMORY_DIRECT_VERBS,
    _OUTPUT_GRAMMAR_WORDS,
    _PUNCTUATION,
    _QUOTE_PAIRS,
    _VALIDATION_TAILS,
)
from agent.planning.task_semantics_positive_proof_lexing import (
    _bounded_symbol,
    _path_value,
)
from agent.planning.task_semantics_positive_proof_model import _Lexeme


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


def _source_only_output(values: Sequence[str], lexemes: Sequence[_Lexeme]) -> bool:
    paths = tuple(
        item for item in lexemes if _path_value(item.raw)
    )
    if not paths:
        return False
    non_paths = tuple(
        value
        for value in values[1:]
        if value not in _PUNCTUATION and not _path_value(value)
    )
    return all(value in _OUTPUT_GRAMMAR_WORDS for value in non_paths) and not any(
        value in _DESTINATION_RELATIONS for value in non_paths
    )


def _supported_prefix(values: Sequence[str]) -> bool:
    prefix = tuple(value for value in values if value not in _PUNCTUATION)
    return prefix in _DIRECT_REQUEST_PREFIXES


__all__ = [
    "_memory_fragment",
    "_memory_payload_supported",
    "_mutation_tail_supported",
    "_source_only_output",
    "_supported_prefix",
]
