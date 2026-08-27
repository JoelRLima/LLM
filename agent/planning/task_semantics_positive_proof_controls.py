"""Bounded inert and read-context productions."""

from __future__ import annotations

from typing import Sequence

from agent.planning.task_semantics_positive_proof_data import (
    _NEGATION_TOKENS,
    _READ_VERBS,
    _RESPONSE_VERBS,
)
from agent.planning.task_semantics_positive_proof_lexing import (
    _paths,
    _trim_punctuation,
)
from agent.planning.task_semantics_positive_proof_model import _Lexeme


def _safe_inert_fragment(values: Sequence[str]) -> bool:
    if not values:
        return True
    if values[0] in _READ_VERBS | _RESPONSE_VERBS:
        return not any(value in _NEGATION_TOKENS for value in values)
    if values[:2] in {("primeiro", "leia"), ("first", "read")}:
        return True
    return values[0] == "use" and bool(set(values) & {"como", "fonte", "source"})


def _safe_read_prefix_target(lexemes: Sequence[_Lexeme]) -> str | None:
    cleaned = _trim_punctuation(lexemes)
    values = tuple(item.value for item in cleaned)
    if not values:
        return None
    paths = _paths(cleaned)
    if len(paths) != 1:
        return None
    if values[:2] in {("primeiro", "leia"), ("first", "read")}:
        return paths[0]
    return None


__all__ = ["_safe_inert_fragment", "_safe_read_prefix_target"]
