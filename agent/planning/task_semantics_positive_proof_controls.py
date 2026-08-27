"""The single bounded neutral-context production owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from agent.planning.task_semantics_positive_proof_data import (
    _DESTINATION_RELATIONS,
    _OUTPUT_GRAMMAR_WORDS,
    _OUTPUT_VERBS,
    _PUNCTUATION,
    _READ_VERBS,
    _RESPONSE_VERBS,
)
from agent.planning.task_semantics_positive_proof_lexing import (
    _path_value,
    _trim_punctuation,
)
from agent.planning.task_semantics_positive_proof_model import _Lexeme
from agent.resources.contracts import WORKSPACE_RESOURCE, normalize_resource_id


@dataclass(frozen=True, slots=True)
class _NeutralContextSpec:
    """Auditable match for one complete, bounded neutral fragment."""

    production_id: str
    span: tuple[int, int]
    target: str
    arguments: tuple[tuple[str, str], ...]
    consumed_tokens: tuple[str, ...]


def _parse_neutral_fragment(
    lexemes: Sequence[_Lexeme],
) -> _NeutralContextSpec | None:
    """Match only the finite neutral productions owned by the authority grammar."""

    cleaned = _trim_punctuation(lexemes)
    if not cleaned:
        return None
    values = tuple(item.value for item in cleaned)
    for parser in (
        _parse_exact_read_or_response,
        _parse_first_read,
        _parse_exact_source,
        _parse_source_only_output,
    ):
        spec = parser(cleaned, values)
        if spec is not None:
            return spec
    return None


def _parse_exact_read_or_response(
    cleaned: Sequence[_Lexeme], values: tuple[str, ...]
) -> _NeutralContextSpec | None:
    if len(values) != 2 or not _path_value(cleaned[1].raw):
        return None
    if values[0] in _READ_VERBS:
        production_id = "NEUTRAL_READ_EXACT_PATH_V1"
    elif values[0] in _RESPONSE_VERBS - {"use"}:
        production_id = "NEUTRAL_RESPONSE_EXACT_PATH_V1"
    else:
        return None
    return _make_neutral_spec(cleaned, 1, production_id)


def _parse_first_read(
    cleaned: Sequence[_Lexeme], values: tuple[str, ...]
) -> _NeutralContextSpec | None:
    if (
        len(values) != 3
        or values[:2] not in {("primeiro", "leia"), ("first", "read")}
        or not _path_value(cleaned[2].raw)
    ):
        return None
    return _make_neutral_spec(cleaned, 2, "NEUTRAL_FIRST_READ_EXACT_PATH_V1")


def _parse_exact_source(
    cleaned: Sequence[_Lexeme], values: tuple[str, ...]
) -> _NeutralContextSpec | None:
    if (
        len(values) != 4
        or values[0] != "use"
        or not _path_value(cleaned[1].raw)
        or values[2:] not in {("as", "source"), ("como", "fonte")}
    ):
        return None
    return _make_neutral_spec(cleaned, 1, "NEUTRAL_SOURCE_EXACT_PATH_V1")


def _parse_source_only_output(
    cleaned: Sequence[_Lexeme], values: tuple[str, ...]
) -> _NeutralContextSpec | None:
    if not values or values[0] not in _OUTPUT_VERBS:
        return None
    path_items = tuple(item for item in cleaned[1:] if _path_value(item.raw))
    non_paths = tuple(
        value
        for value in values[1:]
        if value not in _PUNCTUATION and not _path_value(value)
    )
    if len(path_items) != 1:
        return None
    if not all(value in _OUTPUT_GRAMMAR_WORDS for value in non_paths):
        return None
    if any(value in _DESTINATION_RELATIONS for value in non_paths):
        return None
    target_index = next(
        index for index, item in enumerate(cleaned) if item is path_items[0]
    )
    return _make_neutral_spec(cleaned, target_index, "NEUTRAL_SOURCE_ONLY_OUTPUT_V1")


def _make_neutral_spec(
    cleaned: Sequence[_Lexeme], target_index: int, production_id: str
) -> _NeutralContextSpec | None:
    target = normalize_resource_id(cleaned[target_index].raw)
    if target == WORKSPACE_RESOURCE:
        return None
    return _NeutralContextSpec(
        production_id=production_id,
        span=(cleaned[0].start, cleaned[-1].end),
        target=target,
        arguments=(("source", target),),
        consumed_tokens=tuple(item.value for item in cleaned),
    )


__all__ = ["_NeutralContextSpec", "_parse_neutral_fragment"]
