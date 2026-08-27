"""Lexical and structural helpers for the closed positive grammar."""

from __future__ import annotations

import re
from typing import Sequence

from agent.planning.task_semantics_normalization import _normalize_text
from agent.planning.task_semantics_positive_proof_data import (
    _AUTHORITY_VERBS,
    _LEXEME_RE,
    _NEGATION_TOKENS,
    _PATH_RE,
    _PUNCTUATION,
    _QUOTE_PAIRS,
)
from agent.planning.task_semantics_positive_proof_model import _Lexeme
from agent.resources.contracts import WORKSPACE_RESOURCE, normalize_resource_id


def _split_control_segments(lexemes: Sequence[_Lexeme]) -> tuple[tuple[_Lexeme, ...], ...]:
    segments: list[tuple[_Lexeme, ...]] = []
    start = 0
    for index, item in enumerate(lexemes):
        if item.value == ";" or item.value in {"but", "mas"}:
            segment = tuple(lexemes[start:index])
            if _trim_punctuation(segment):
                segments.append(segment)
            start = index + 1
    final = tuple(lexemes[start:])
    if _trim_punctuation(final):
        segments.append(final)
    return tuple(segments)


def _split_conjoined_commands(lexemes: Sequence[_Lexeme]) -> tuple[tuple[_Lexeme, ...], ...]:
    values = tuple(item.value for item in lexemes)
    split_indices = [
        index
        for index, value in enumerate(values)
        if value in {"and", "e"}
        and not (
            value == "e"
            and index > 0
            and values[index - 1] in _NEGATION_TOKENS
            and index + 1 < len(values)
            and values[index + 1] in {"para", "to"}
        )
        and any(later in _AUTHORITY_VERBS for later in values[index + 1 :])
    ]
    if not split_indices:
        return (tuple(lexemes),)
    result: list[tuple[_Lexeme, ...]] = []
    start = 0
    for index in split_indices:
        result.append(tuple(lexemes[start:index]))
        start = index + 1
    result.append(tuple(lexemes[start:]))
    return tuple(item for item in result if _trim_punctuation(item))


def _remove_bounded_parenthetical(lexemes: Sequence[_Lexeme]) -> tuple[_Lexeme, ...]:
    values = tuple(item.value for item in lexemes)
    if "(" not in values and ")" not in values:
        return tuple(lexemes)
    try:
        start = values.index("(")
        end = values.index(")", start + 1)
    except ValueError:
        return ()
    inside = values[start + 1 : end]
    prefixes = (
        ("o", "valor", "esperado", "neste", "caso", "e"),
        ("o", "valor", "observado", "neste", "caso", "e"),
    )
    if not any(inside[: len(prefix)] == prefix for prefix in prefixes):
        return ()
    if len(inside) != 7 or not _bounded_symbol(inside[-1]):
        return ()
    return tuple((*lexemes[:start], *lexemes[end + 1 :]))


def _contains_quoted_command(text: str) -> bool:
    for opening, closing in _QUOTE_PAIRS:
        start = 0
        while True:
            left = text.find(opening, start)
            if left < 0:
                break
            right = text.find(closing, left + len(opening))
            if right < 0:
                break
            values = {item.value for item in _lexemes(text[left + len(opening) : right])}
            if values & _AUTHORITY_VERBS:
                return True
            start = right + len(closing)
    return False


def _lexemes(text: str) -> tuple[_Lexeme, ...]:
    repaired = _repair_mojibake(text)
    lexical = repaired.casefold().replace("don't", "do not").replace("dont", "do not")
    return tuple(
        _Lexeme(_normalize_text(match.group(0)), match.group(0), match.start(), match.end())
        for match in _LEXEME_RE.finditer(lexical)
    )


def _trim_punctuation(lexemes: Sequence[_Lexeme]) -> tuple[_Lexeme, ...]:
    result = tuple(lexemes)
    while result and result[0].value in _PUNCTUATION | {";"}:
        result = result[1:]
    while result and result[-1].value in _PUNCTUATION | {";"}:
        result = result[:-1]
    return result


def _paths(lexemes: Sequence[_Lexeme]) -> tuple[str, ...]:
    result: list[str] = []
    for item in lexemes:
        if not _path_value(item.raw):
            continue
        normalized = normalize_resource_id(item.raw)
        if normalized != WORKSPACE_RESOURCE and normalized not in result:
            result.append(normalized)
    return tuple(result)


def _single_path(lexemes: Sequence[_Lexeme]) -> str | None:
    paths = _paths(lexemes)
    return paths[0] if len(paths) == 1 else None


def _path_value(value: str) -> bool:
    return bool(_PATH_RE.fullmatch(value.strip(".,;:()[]{}")))


def _bounded_symbol(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9_./-]{1,128}", value))


def _repair_mojibake(text: str) -> str:
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        repaired = text
    if repaired == text:
        repaired = repaired.replace("n\ufffdo", "nao").replace(
            "contr\ufffdrio", "contrario"
        )
    return repaired


__all__ = [
    "_bounded_symbol",
    "_contains_quoted_command",
    "_lexemes",
    "_paths",
    "_path_value",
    "_repair_mojibake",
    "_remove_bounded_parenthetical",
    "_single_path",
    "_split_conjoined_commands",
    "_split_control_segments",
    "_trim_punctuation",
]
