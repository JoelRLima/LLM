"""Bounded lexical support for canonical negative authority constraints."""

from __future__ import annotations

from typing import Callable, Sequence

from agent.planning.task_semantics_positive_proof_command_support import (
    _memory_payload_supported,
)
from agent.planning.task_semantics_positive_proof_data import (
    _AUTHORITY_VERBS,
    _MEMORY_CONTEXT_VERBS,
    _MEMORY_DIRECT_VERBS,
    _MUTATION_VERBS,
    _NEGATION_TOKENS,
    _OUTPUT_VERBS,
    _PUNCTUATION,
)
from agent.planning.task_semantics_positive_proof_lexing import _path_value
from agent.planning.task_semantics_positive_proof_model import (
    _ConstraintSpec,
    _Lexeme,
    _Predicate,
    _ProofSpec,
)

_NEGATIVE_GLOBAL_TAILS = frozenset(
    {
        (),
        ("nada",),
        ("nothing",),
        ("anything",),
        ("arquivo",),
        ("arquivos",),
        ("file",),
        ("files",),
        ("a", "file"),
        ("the", "file"),
        ("a", "arquivo"),
        ("os", "arquivos"),
        ("the", "files"),
        ("nenhum", "arquivo"),
        ("nenhum", "arquivos"),
        ("any", "file"),
        ("any", "files"),
        ("all", "files"),
        ("every", "file"),
        ("todo", "arquivo"),
        ("todos", "arquivos"),
        ("todos", "os", "arquivos"),
    }
)

_NEGATIVE_PREFIXES = frozenset(
    {
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
)


def _negative_global_tail_supported(values: Sequence[str]) -> bool:
    tail = tuple(value for value in values if value not in _PUNCTUATION)
    return tail in _NEGATIVE_GLOBAL_TAILS


def _trim_negative_prefix(values: Sequence[str]) -> tuple[str, ...]:
    prefix = tuple(values)
    while prefix[:1] in {("please",), ("kindly",), ("por",)}:
        prefix = prefix[1:]
    if prefix[:2] == ("por", "favor"):
        prefix = prefix[2:]
    return prefix


def _parse_negative_fragment(
    lexemes: Sequence[_Lexeme],
    predicate: _Predicate | None,
    *,
    parse_output_command: Callable[..., _ProofSpec | None],
    parse_mutation_command: Callable[..., _ProofSpec | None],
) -> _ConstraintSpec | None:
    values = tuple(item.value for item in lexemes if item.value not in _PUNCTUATION)
    verb_index = next(
        (index for index, value in enumerate(values) if value in _AUTHORITY_VERBS),
        None,
    )
    if verb_index is None:
        return None
    prefix = _trim_negative_prefix(values[:verb_index])
    if not _negative_prefix_supported(prefix):
        return None
    after = values[verb_index + 1 :]
    tail_lexemes = tuple(
        item for item in lexemes if item.value not in _PUNCTUATION
    )[verb_index:]
    memory = _parse_negative_memory_constraint(values, tail_lexemes, verb_index, predicate)
    if memory is not None:
        return memory
    return _parse_negative_write_constraint(
        after,
        tail_lexemes,
        predicate,
        parse_output_command=parse_output_command,
        parse_mutation_command=parse_mutation_command,
    )


def _negative_prefix_supported(prefix: tuple[str, ...]) -> bool:
    return bool(set(prefix) & _NEGATION_TOKENS) and prefix in _NEGATIVE_PREFIXES


def _parse_negative_memory_constraint(
    values: Sequence[str],
    tail_lexemes: Sequence[_Lexeme],
    verb_index: int,
    predicate: _Predicate | None,
) -> _ConstraintSpec | None:
    after = values[verb_index + 1 :]
    verb = values[verb_index]
    memory_scope = bool(set(after) & {"memory", "memoria"}) and not any(
        _path_value(item.raw) for item in tail_lexemes[1:]
    )
    is_memory_verb = verb in _MEMORY_DIRECT_VERBS or (
        verb in _MEMORY_CONTEXT_VERBS | _OUTPUT_VERBS and memory_scope
    )
    if not is_memory_verb:
        return None
    if verb in _MEMORY_DIRECT_VERBS and after and not _memory_payload_supported(values[verb_index:]):
        return None
    if verb in _MEMORY_CONTEXT_VERBS and after and not (set(after) & {"memory", "memoria"}):
        return None
    return _ConstraintSpec("memory_write", "memory", "MEMORY_CONSTRAINT_V1", "MEMORY", predicate)


def _parse_negative_write_constraint(
    after: Sequence[str],
    tail_lexemes: Sequence[_Lexeme],
    predicate: _Predicate | None,
    *,
    parse_output_command: Callable[..., _ProofSpec | None],
    parse_mutation_command: Callable[..., _ProofSpec | None],
) -> _ConstraintSpec | None:
    verb = tail_lexemes[0].value if tail_lexemes else ""
    candidate: _ProofSpec | None = None
    if verb in _OUTPUT_VERBS:
        candidate = parse_output_command(tail_lexemes, 0, predicate)
    if candidate is None and verb in _MUTATION_VERBS | _OUTPUT_VERBS:
        candidate = parse_mutation_command(
            tail_lexemes,
            0,
            fallback_target=None,
            predicate=predicate,
        )
    if candidate is not None:
        return _ConstraintSpec(
            candidate.effect,
            candidate.target,
            "WRITE_CONSTRAINT_EXACT_V1",
            candidate.target_role,
            predicate,
        )
    if _negative_global_tail_supported(after):
        return _ConstraintSpec("write", "*", "WRITE_CONSTRAINT_GLOBAL_V1", "WORKSPACE", predicate)
    return None


__all__ = ["_parse_negative_fragment"]
