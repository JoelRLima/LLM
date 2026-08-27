"""Command productions for the closed positive authority grammar."""

from __future__ import annotations

from typing import Sequence

from agent.planning.task_semantics_positive_proof_command_support import (
    _memory_fragment,
    _memory_payload_supported,
    _mutation_tail_supported,
    _source_only_output,
    _supported_prefix,
)
from agent.planning.task_semantics_positive_proof_controls import _safe_inert_fragment
from agent.planning.task_semantics_positive_proof_data import (
    _ARTICLES,
    _AUTHORITY_VERBS,
    _MUTATION_VERBS,
    _NEGATION_TOKENS,
    _OUTPUT_GRAMMAR_WORDS,
    _OUTPUT_VERBS,
    _PUNCTUATION,
)
from agent.planning.task_semantics_positive_proof_lexing import (
    _bounded_symbol,
    _path_value,
    _paths,
    _trim_punctuation,
)
from agent.planning.task_semantics_positive_proof_model import _Lexeme, _Predicate, _ProofSpec
from agent.resources.contracts import normalize_resource_id


def _parse_fragment(
    lexemes: Sequence[_Lexeme],
    *,
    fallback_target: str | None,
    predicate: _Predicate | None,
) -> tuple[tuple[_ProofSpec, ...], bool]:
    cleaned = _trim_punctuation(lexemes)
    if not cleaned or _is_negative_fragment(cleaned):
        return ((), True)
    values = tuple(item.value for item in cleaned)
    if _memory_fragment(values):
        return _parse_memory_fragment(values, predicate)
    verb_index = next(
        (index for index, value in enumerate(values) if value in _MUTATION_VERBS | _OUTPUT_VERBS),
        None,
    )
    if verb_index is None:
        return ((), _safe_inert_fragment(values))
    return _parse_verb_fragment(cleaned, values, verb_index, fallback_target, predicate)


def _parse_memory_fragment(
    values: Sequence[str], predicate: _Predicate | None
) -> tuple[tuple[_ProofSpec, ...], bool]:
    if not _memory_payload_supported(values):
        return ((), False)
    return (
        (_ProofSpec("memory_write", "memory", "MEMORY_DIRECT_V1", "MEMORY", predicate),),
        True,
    )


def _parse_verb_fragment(
    cleaned: Sequence[_Lexeme],
    values: Sequence[str],
    verb_index: int,
    fallback_target: str | None,
    predicate: _Predicate | None,
) -> tuple[tuple[_ProofSpec, ...], bool]:
    if not _supported_prefix(values[:verb_index]):
        return ((), False)
    verb = values[verb_index]
    if verb in _OUTPUT_VERBS:
        output = _parse_output_command(cleaned, verb_index, predicate)
        if output is not None:
            return ((output,), True)
        if _source_only_output(values[verb_index:], cleaned[verb_index:]):
            return ((), True)
        if predicate is not None:
            symbolic_output = _parse_mutation_command(
                cleaned,
                verb_index,
                fallback_target=None,
                predicate=predicate,
            )
            if symbolic_output is not None:
                return ((symbolic_output,), True)
        if verb not in _MUTATION_VERBS:
            return ((), False)
    mutation = _parse_mutation_command(
        cleaned,
        verb_index,
        fallback_target=fallback_target,
        predicate=predicate,
    )
    return ((mutation,), True) if mutation is not None else ((), False)


def _parse_mutation_command(
    lexemes: Sequence[_Lexeme],
    verb_index: int,
    *,
    fallback_target: str | None,
    predicate: _Predicate | None,
) -> _ProofSpec | None:
    after = lexemes[verb_index + 1 :]
    paths = _paths(after)
    target = paths[0] if paths else fallback_target
    target_index = next(
        (index for index in range(verb_index + 1, len(lexemes)) if _path_value(lexemes[index].raw)),
        None,
    )
    if target is None:
        symbolic = tuple(
            item.value
            for item in after
            if item.value not in _ARTICLES and item.value not in _PUNCTUATION
        )
        if predicate is None or len(symbolic) != 1 or not _bounded_symbol(symbolic[0]):
            return None
        target = symbolic[0]
        tail: tuple[str, ...] = ()
    else:
        if target_index is None:
            tail = tuple(item.value for item in after if item.value not in _PUNCTUATION)
        else:
            before_target = tuple(
                item.value
                for item in lexemes[verb_index + 1 : target_index]
                if item.value not in _PUNCTUATION
            )
            if before_target and not all(value in _ARTICLES for value in before_target):
                return None
            tail = tuple(
                item.value
                for item in lexemes[target_index + 1 :]
                if item.value not in _PUNCTUATION
            )
    if not _mutation_tail_supported(tail):
        return None
    production = "WRITE_MUTATION_EXACT_V1" if predicate is None else "WRITE_CONDITIONAL_EXACT_V1"
    return _ProofSpec("write", normalize_resource_id(target), production, "MUTATION_TARGET", predicate)


def _parse_output_command(
    lexemes: Sequence[_Lexeme],
    verb_index: int,
    predicate: _Predicate | None,
) -> _ProofSpec | None:
    values = tuple(item.value for item in lexemes)
    paths = [
        (index, normalize_resource_id(item.raw))
        for index, item in enumerate(lexemes)
        if index > verb_index and _path_value(item.raw)
    ]
    target: str | None = None
    role = "DESTINATION"
    for index, path in reversed(paths):
        preceding = set(values[verb_index + 1 : index])
        if preceding & {"em", "in", "into", "na", "no", "to"}:
            target = path
            break
    if target is None and paths:
        first_index, first_path = paths[0]
        between = tuple(values[verb_index + 1 : first_index])
        if not between or all(value in _ARTICLES for value in between):
            target = first_path
            role = "MUTATION_TARGET"
    if target is None:
        return None
    grammar_values = tuple(
        value
        for value in values[verb_index + 1 :]
        if value not in _PUNCTUATION and not _path_value(value)
    )
    if any(value not in _OUTPUT_GRAMMAR_WORDS for value in grammar_values):
        return None
    production = "WRITE_OUTPUT_DESTINATION_V1" if predicate is None else "WRITE_OUTPUT_CONDITIONAL_V1"
    return _ProofSpec("write", target, production, role, predicate)


def _is_negative_fragment(lexemes: Sequence[_Lexeme]) -> bool:
    values = tuple(item.value for item in lexemes if item.value not in _PUNCTUATION)
    verb_index = next(
        (index for index, value in enumerate(values) if value in _AUTHORITY_VERBS),
        None,
    )
    if verb_index is None:
        return False
    prefix = _trim_negative_prefix(values[:verb_index])
    if not any(value in _NEGATION_TOKENS for value in prefix):
        return False
    accepted_prefixes = {
        ("nao",),
        ("never",),
        ("not",),
        ("jamais",),
        ("do", "not"),
        ("e", "proibido"),
        ("is", "prohibited"),
        ("forbidden",),
        ("nao", "e", "para"),
        ("not", "allowed", "to"),
        ("eu", "nao", "quero", "que", "voce"),
        ("i", "do", "not", "want", "you", "to"),
        ("i", "forbid", "you", "to"),
        ("under", "no", "circumstances"),
    }
    if prefix not in accepted_prefixes:
        return False
    after = values[verb_index + 1 :]
    if after in {("nada",), ("nothing",), ("anything",)}:
        return True
    if not after:
        return True
    return _parse_mutation_command(
        lexemes, verb_index, fallback_target=None, predicate=None
    ) is not None


def _trim_negative_prefix(values: Sequence[str]) -> tuple[str, ...]:
    prefix = tuple(values)
    while prefix[:1] in {("please",), ("kindly",), ("por",)}:
        prefix = prefix[1:]
    if prefix[:2] == ("por", "favor"):
        prefix = prefix[2:]
    return prefix


__all__ = ["_parse_fragment"]
