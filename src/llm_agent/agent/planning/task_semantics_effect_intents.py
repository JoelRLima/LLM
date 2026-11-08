"""Projection of objective clauses into effect intent candidates."""

from __future__ import annotations

import re
from collections.abc import Sequence

from llm_agent.agent.planning.task_semantics_effect_data import (
    _EFFECT_TERMS,
    _EXPLICIT_TARGET_WORDS,
    _MEMORY_CONTEXT_TERMS,
    _MEMORY_CONTEXT_WORDS,
    _MEMORY_DIRECT_TERMS,
    _NEGATED_DURABLE_OUTPUT_MARKERS,
    _OBLIGATION_TERMS,
    _OUTPUT_ONLY_NOUNS,
    _OUTPUT_VERBS,
    _PERSISTENCE_VERBS,
    _SEARCH_FILLER,
    PathRole,
    _PathMention,
    _PredicateClaim,
)
from llm_agent.agent.planning.task_semantics_effect_paths import _classify_path_roles, _file_targets
from llm_agent.agent.planning.task_semantics_effect_syntax import (
    _clause_condition,
    _conditional_symbolic_target,
    _is_durable_output_verb,
    _positive_candidate_syntax,
    _predicate_claim,
)
from llm_agent.agent.planning.task_semantics_effect_tokens import (
    _is_direct_text_request,
    _is_negated,
    _repair_mojibake,
    _tokens,
)
from llm_agent.agent.planning.task_semantics_types import EffectIntent, _normalize_text


def _intent_clauses(objective: str) -> tuple[str, ...]:
    repaired = _repair_mojibake(objective)
    clauses = re.split(
        r"(?:;|\bmas\b|\bbut\b)",
        repaired,
        flags=re.IGNORECASE,
    )
    return tuple(item.strip() for item in clauses if item.strip())


def _clause_intents(objective: str) -> tuple[EffectIntent, ...]:
    intents: list[EffectIntent] = []
    previous_predicate: _PredicateClaim | None = None
    for clause in _intent_clauses(objective):
        tokens = _tokens(clause)
        condition = _clause_condition(clause)
        predicate = _predicate_claim(clause, previous_predicate)
        if predicate is not None and not _is_otherwise(tokens):
            previous_predicate = predicate
        else:
            previous_predicate = None
        intents.extend(_derive_clause_intents(clause, tokens, condition, predicate))
    return tuple(intents)


def _is_otherwise(tokens: Sequence[str]) -> bool:
    return ("caso" in tokens and "contrario" in tokens) or "otherwise" in tokens


def _derive_clause_intents(
    clause: str,
    tokens: Sequence[str],
    condition: str | None,
    predicate: _PredicateClaim | None,
) -> tuple[EffectIntent, ...]:
    paths = _file_targets(clause)
    path_roles = _classify_path_roles(clause, tokens)
    memory_indices = _memory_effect_indices(tokens)
    memory_index_set = set(memory_indices)
    intents: list[EffectIntent] = []
    for index, token in enumerate(tokens):
        effect = _EFFECT_TERMS.get(token)
        if effect is None:
            continue
        intents.extend(
            _effect_intents_for_token(
                clause,
                tokens,
                index,
                token,
                effect,
                paths,
                path_roles,
                memory_index_set,
                condition,
                predicate,
            )
        )
    intents.extend(_memory_intent(clause, tokens, index, condition, predicate, path_roles) for index in memory_indices)
    return tuple(intents)


def _effect_intents_for_token(
    clause: str,
    tokens: Sequence[str],
    index: int,
    token: str,
    effect: str,
    paths: tuple[str, ...],
    path_roles: tuple[_PathMention, ...],
    memory_indices: set[int],
    condition: str | None,
    predicate: _PredicateClaim | None,
) -> tuple[EffectIntent, ...]:
    if _is_direct_text_request(tokens, index):
        return ()
    if effect == "write" and index in memory_indices and token in _MEMORY_CONTEXT_TERMS:
        return ()
    if effect == "write" and token in (_OUTPUT_VERBS | _PERSISTENCE_VERBS):
        targets = _output_targets(clause, tokens, index, token, path_roles)
    else:
        targets = _mutation_targets(paths, path_roles, index)
    return tuple(
        _make_effect_intent(clause, tokens, index, effect, target, path_roles, condition, predicate)
        for target in targets
    )


def _output_targets(
    clause: str,
    tokens: Sequence[str],
    index: int,
    token: str,
    path_roles: tuple[_PathMention, ...],
) -> tuple[str, ...]:
    if not _is_durable_output_verb(token, tokens, clause):
        if not (_is_negated(tokens, index) and set(tokens[index + 1 :]) & _NEGATED_DURABLE_OUTPUT_MARKERS):
            return ()
    targets = tuple(
        item.value
        for item in path_roles
        if item.effect_index == index and item.role in {PathRole.DESTINATION, PathRole.MUTATION_TARGET}
    )
    if targets:
        return targets
    symbolic_target = _conditional_symbolic_target(tokens, index)
    if symbolic_target is not None and not _is_negated(tokens, index):
        return (symbolic_target,)
    if _has_explicit_output_target(tokens):
        return ("*",)
    if _is_negated(tokens, index) and set(tokens[index + 1 :]) & _NEGATED_DURABLE_OUTPUT_MARKERS:
        return ("*",)
    return ()


def _has_explicit_output_target(tokens: Sequence[str]) -> bool:
    return any(word in _EXPLICIT_TARGET_WORDS for word in tokens) and not any(
        word in _OUTPUT_ONLY_NOUNS for word in tokens
    )


def _mutation_targets(paths: tuple[str, ...], path_roles: tuple[_PathMention, ...], index: int) -> tuple[str, ...]:
    scoped = tuple(
        item.value for item in path_roles if item.effect_index == index and item.role is PathRole.MUTATION_TARGET
    )
    if scoped:
        return scoped
    if path_roles:
        return ()
    return paths or ("*",)


def _make_effect_intent(
    clause: str,
    tokens: Sequence[str],
    index: int,
    effect: str,
    target: str,
    path_roles: tuple[_PathMention, ...],
    condition: str | None,
    predicate: _PredicateClaim | None,
) -> EffectIntent:
    path_role = next(
        (item.role for item in path_roles if item.value == target and item.effect_index == index),
        PathRole.MUTATION_TARGET if target != "*" and condition is not None else PathRole.UNKNOWN,
    )
    return EffectIntent(
        effect=effect,
        target=target,
        polarity="prohibited" if _is_negated(tokens, index) else "requested",
        condition=condition,
        predicate_id=predicate.predicate_id if predicate is not None else None,
        predicate_expected=predicate.expected if predicate is not None else None,
        candidate_role=path_role.value,
        positive_syntax=_positive_candidate_syntax(clause, tokens, index, target, path_role, condition, path_roles),
    )


def _memory_intent(
    clause: str,
    tokens: Sequence[str],
    index: int,
    condition: str | None,
    predicate: _PredicateClaim | None,
    path_roles: tuple[_PathMention, ...],
) -> EffectIntent:
    return EffectIntent(
        effect="memory_write",
        target="memory",
        polarity="prohibited" if _is_negated(tokens, index) else "requested",
        condition=condition,
        predicate_id=predicate.predicate_id if predicate is not None else None,
        predicate_expected=predicate.expected if predicate is not None else None,
        candidate_role=PathRole.MEMORY.value,
        positive_syntax=_positive_candidate_syntax(
            clause, tokens, index, "memory", PathRole.MEMORY, condition, path_roles
        ),
    )


def _near_memory_context(tokens: Sequence[str], index: int) -> bool:
    start = max(0, index - 2)
    end = min(len(tokens), index + 5)
    return bool(set(tokens[start:end]) & _MEMORY_CONTEXT_WORDS)


def _memory_effect_indices(tokens: Sequence[str]) -> tuple[int, ...]:
    return tuple(
        index
        for index, token in enumerate(tokens)
        if token in _MEMORY_DIRECT_TERMS or (token in _MEMORY_CONTEXT_TERMS and _near_memory_context(tokens, index))
    )


def _search_spec(objective: str, tokens: Sequence[str]) -> tuple[str | None, str | None] | None:
    normalized_objective = _normalize_text(objective)
    for index, token in enumerate(tokens):
        if _OBLIGATION_TERMS.get(token) != "search" or _is_negated(tokens, index):
            continue
        candidates = tokens[index + 1 : index + 8]
        for candidate_index, candidate in enumerate(candidates):
            if candidate not in _SEARCH_FILLER and candidate not in _OBLIGATION_TERMS:
                previous = candidates[candidate_index - 1] if candidate_index else None
                if candidate == "valor" and (
                    "valor observado" in normalized_objective or previous in {"esse", "essa", "este", "esta"}
                ):
                    return None, "previous_read"
                return candidate, None
        if any(
            phrase in normalized_objective
            for phrase in ("palavra que ele contem", "texto observado", "valor observado", "use o texto observado")
        ):
            return None, "previous_read"
        # A generic search request has no bounded identity to bind. Do not
        # manufacture a previous-read obligation that the task never asked for.
        return None
    return None
