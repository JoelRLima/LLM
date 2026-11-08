"""Objective text tokenization and negation recognition."""

from __future__ import annotations

from collections.abc import Sequence

from llm_agent.agent.planning.task_semantics_effect_data import (
    _COPULA_TOKEN,
    _DIRECT_TEXT_WORDS,
    _NEGATION_WORDS,
    _PREDICATE_OPERATOR_TOKENS,
    _PROHIBITION_WORDS,
    _WORD_RE,
)
from llm_agent.agent.planning.task_semantics_types import _normalize_text


def _tokens(objective: str) -> tuple[str, ...]:
    objective = _repair_mojibake(objective)
    lexical = objective.casefold().replace("\u00e2\u20ac\u2122", "'")
    lexical = lexical.replace("don't", "do not").replace("dont", "do not")
    # Preserve authority-bearing syntax before accent-insensitive matching.
    # Portuguese ``é`` is a copula, not the conjunction ``e``; collapsing the
    # two made an earlier ``não`` fall outside the governed effect clause.
    return tuple(_COPULA_TOKEN if token == "é" else _normalize_text(token) for token in _WORD_RE.findall(lexical))


def _repair_mojibake(text: str) -> str:
    """Recover one common UTF-8-as-Latin-1 layer without changing valid text."""

    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text
    return repaired if repaired != text else text


def _is_negated(tokens: Sequence[str], index: int) -> bool:
    """Resolve bounded clause-level prohibition scope for one effect verb.

    The old suffix-window rule lost ``nao`` as soon as a subject or modal was
    inserted between it and the effect.  We retain a bounded deterministic
    grammar: inspect the current conjunction segment, then accept a
    prohibition cue anywhere before the governed effect.  A new ``e``/``and``
    segment starts a fresh scope, so ``nao edite a.py e edite b.py`` does not
    prohibit the second operation.
    """

    if index < 0 or index >= len(tokens):
        return False
    start = 0
    condition_start = _condition_start(tokens)
    if condition_start is not None and condition_start < len(tokens):
        operator_index = next(
            (
                position
                for position in range(condition_start + 1, index)
                if tokens[position] in _PREDICATE_OPERATOR_TOKENS
            ),
            None,
        )
        if operator_index is not None:
            value_index = operator_index + 1
            if value_index < index and tokens[value_index] in _DIRECT_TEXT_WORDS:
                value_index += 1
            # Predicate negation is consumed by predicate truth. Effect
            # prohibition begins only after the predicate literal.
            start = min(index, value_index + 1)
    for position in range(index - 1, -1, -1):
        if tokens[position] in {"e", "and"}:
            start = max(start, position + 1)
            break
    window = tuple(tokens[start:index])
    if any(token in _PROHIBITION_WORDS for token in window):
        return True
    if any(token in _NEGATION_WORDS or token in {"dont", "not", "cannot"} for token in window):
        return True
    patterns = (
        ("do", "not"),
        ("must", "not"),
        ("should", "not"),
        ("nao", "deve"),
        ("nao", "quero"),
        ("nao", "pode"),
    )
    if any(
        len(pattern) <= len(window)
        and any(
            tuple(window[offset : offset + len(pattern)]) == pattern for offset in range(len(window) - len(pattern) + 1)
        )
        for pattern in patterns
    ):
        return True

    # A prohibition can govern an operation from the right as well as from
    # the left.  This is intentionally a small structural check, not a claim
    # that the deny vocabulary is exhaustive.  The positive-admission kernel
    # remains fail-closed when an unfamiliar constraint is not recognized.
    suffix = tuple(tokens[index + 1 :])
    postposed_patterns = (
        ("is", "prohibited"),
        ("is", "forbidden"),
        ("is", "disallowed"),
        ("is", "not", "allowed"),
        ("is", "not", "permitted"),
        ("is", "not", "authorized"),
        ("not", "allowed"),
        ("not", "permitted"),
        ("not", "authorized"),
        ("e", "proibido"),
        ("e", "proibida"),
        (_COPULA_TOKEN, "proibido"),
        (_COPULA_TOKEN, "proibida"),
        ("nao", "e", "permitido"),
        ("nao", "e", "permitida"),
        ("nao", _COPULA_TOKEN, "permitido"),
        ("nao", _COPULA_TOKEN, "permitida"),
        ("under", "no", "circumstances"),
        ("under", "no", "account"),
        ("by", "no", "means"),
    )
    return any(
        len(pattern) <= len(suffix)
        and any(
            tuple(suffix[offset : offset + len(pattern)]) == pattern for offset in range(len(suffix) - len(pattern) + 1)
        )
        for pattern in postposed_patterns
    )


def _is_direct_text_request(tokens: Sequence[str], index: int) -> bool:
    return index + 1 < len(tokens) and tokens[index + 1] in _DIRECT_TEXT_WORDS


def _condition_start(tokens: Sequence[str]) -> int | None:
    """Locate a supported conditional marker in a bounded clause."""

    label_offset = 0
    initial = next(
        (index for index in range(label_offset, len(tokens)) if tokens[index] in {"se", "if"}),
        None,
    )
    if initial is None:
        return None
    if initial == label_offset:
        return initial
    if any(token in _PREDICATE_OPERATOR_TOKENS for token in tokens[initial + 1 :]):
        return initial
    return None
