"""Supported predicate and conditional productions."""

from __future__ import annotations

import hashlib
from typing import Sequence

from agent.planning.task_semantics_normalization import _normalize_text
from agent.planning.task_semantics_positive_proof_commands import (
    _is_negative_fragment,
    _parse_fragment,
)
from agent.planning.task_semantics_positive_proof_controls import _safe_read_prefix_target
from agent.planning.task_semantics_positive_proof_data import _PUNCTUATION
from agent.planning.task_semantics_positive_proof_lexing import (
    _bounded_symbol,
    _paths,
    _trim_punctuation,
)
from agent.planning.task_semantics_positive_proof_model import _Lexeme, _Predicate, _ProofSpec
from agent.resources.contracts import normalize_resource_id


def _parse_conditional(
    lexemes: Sequence[_Lexeme],
) -> tuple[tuple[_ProofSpec, ...], bool] | None:
    values = tuple(item.value for item in lexemes)
    condition_start = next(
        (index for index, value in enumerate(values) if value in {"if", "se"}),
        None,
    )
    if condition_start is None:
        return None
    parsed = _conditional_predicate(lexemes, values, condition_start)
    if parsed is None:
        return ((), False)
    predicate, comma, symbolic = parsed
    semicolon = next(
        (index for index in range(comma + 1, len(values)) if values[index] == ";"),
        len(values),
    )
    if symbolic:
        predicate = _symbolic_predicate(
            predicate,
            lexemes[condition_start:comma],
            lexemes[comma + 1 : semicolon],
        )
    true_specs, true_complete = _parse_fragment(
        lexemes[comma + 1 : semicolon],
        fallback_target=predicate.target,
        predicate=predicate,
    )
    if not true_complete:
        return ((), False)
    return _parse_conditional_else(values, lexemes, semicolon, predicate, true_specs)


def _conditional_predicate(
    lexemes: Sequence[_Lexeme], values: Sequence[str], condition_start: int
) -> tuple[_Predicate, int, bool] | None:
    prefix = lexemes[:condition_start]
    fallback_target = _safe_read_prefix_target(prefix)
    if prefix and fallback_target is None:
        return None
    try:
        comma = values.index(",", condition_start + 1)
    except ValueError:
        return None
    predicate = _parse_predicate(lexemes[condition_start:comma], fallback_target)
    if predicate is None:
        return None
    if _paths(lexemes[condition_start:comma]) or fallback_target is not None:
        return predicate, comma, False
    return predicate, comma, True


def _symbolic_predicate(
    predicate: _Predicate,
    condition: Sequence[_Lexeme],
    true_branch: Sequence[_Lexeme],
) -> _Predicate:
    symbolic_material = " ".join(
        item.value
        for item in (*condition, *true_branch)
        if item.value not in _PUNCTUATION
    )
    symbolic = "condition:" + hashlib.sha256(
        _normalize_text(symbolic_material).encode("utf-8")
    ).hexdigest()[:16]
    return _Predicate(symbolic, predicate.expected, symbolic_material, predicate.target)


def _parse_conditional_else(
    values: Sequence[str],
    lexemes: Sequence[_Lexeme],
    semicolon: int,
    predicate: _Predicate,
    true_specs: Sequence[_ProofSpec],
) -> tuple[tuple[_ProofSpec, ...], bool]:
    collected_specs = list(true_specs)
    if semicolon == len(values):
        return (tuple(collected_specs), True)
    remaining = _trim_punctuation(lexemes[semicolon + 1 :])
    remaining_values = tuple(item.value for item in remaining)
    if remaining_values[:2] == ("caso", "contrario"):
        remaining = _trim_punctuation(remaining[2:])
    elif remaining_values[:1] == ("otherwise",):
        remaining = _trim_punctuation(remaining[1:])
    else:
        return ((), False)
    complement = _Predicate(
        predicate.predicate_id,
        not predicate.expected,
        " ".join(item.value for item in remaining),
        predicate.target,
    )
    if _is_negative_fragment(remaining):
        return (tuple(collected_specs), True)
    false_specs, false_complete = _parse_fragment(
        remaining,
        fallback_target=predicate.target,
        predicate=complement,
    )
    if not false_complete:
        return ((), False)
    collected_specs.extend(false_specs)
    return (tuple(collected_specs), True)


def _parse_predicate(
    lexemes: Sequence[_Lexeme],
    fallback_target: str | None,
) -> _Predicate | None:
    cleaned = _remove_bounded_parenthetical(_trim_punctuation(lexemes))
    values = tuple(item.value for item in cleaned)
    if not values or values[0] not in {"if", "se"}:
        return None
    operators = {
        "contain": "contains",
        "contains": "contains",
        "contem": "contains",
        "conter": "contains",
        "contiver": "contains",
        "equal": "equals",
        "equals": "equals",
        "for": "equals",
        "is": "equals",
    }
    operator_index = next(
        (index for index in range(1, len(values)) if values[index] in operators),
        None,
    )
    if operator_index is None:
        return None
    literal_index = operator_index + 1
    while literal_index < len(values) and values[literal_index] in {
        "'",
        "\"",
        "exactly",
        "exatamente",
    }:
        literal_index += 1
    literal_values = tuple(
        value
        for value in values[literal_index:]
        if value not in {"'", "\"", "`", "\u201c", "\u201d", "\u2018", "\u2019"}
    )
    if len(literal_values) != 1:
        return None
    paths = _paths(cleaned[:operator_index])
    target = paths[0] if paths else fallback_target
    if target is None:
        subject = tuple(
            value
            for value in values[1:operator_index]
            if value not in {"a", "conteudo", "content", "do", "of", "o", "the"}
            and value not in {"nao", "not", "never"}
        )
        if len(subject) != 1 or not _bounded_symbol(subject[0]):
            return None
        target = subject[0]
    operator = operators[values[operator_index]]
    literal = literal_values[0]
    expected = not any(
        value in {"nao", "not", "never"} for value in values[1:operator_index]
    )
    predicate_id = f"{normalize_resource_id(target)}|{operator}|{literal}"
    condition = " ".join(values)
    return _Predicate(predicate_id, expected, condition, normalize_resource_id(target))


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


__all__ = ["_parse_conditional"]
