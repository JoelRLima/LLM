"""Conditional branches and positive effect syntax classification."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from llm_agent.agent.planning.task_semantics_effect_data import (
    _AMBIGUITY_WORDS,
    _COPULA_TOKEN,
    _DIRECT_TEXT_WORDS,
    _EXPLICIT_TARGET_WORDS,
    _GOVERNED_POSITIVE_WORDS,
    _IMPERATIVE_FILLERS,
    _MEMORY_CONTEXT_TERMS,
    _MEMORY_DIRECT_TERMS,
    _MUTATION_VERBS,
    _NEGATION_WORDS,
    _OUTPUT_ONLY_NOUNS,
    _OUTPUT_VERBS,
    _PERSISTENCE_VERBS,
    _PROHIBITION_WORDS,
    _UNSUPPORTED_SCOPE_WORDS,
    MAX_CONDITION_TOKENS,
    PathRole,
    _PathMention,
    _PredicateClaim,
)
from llm_agent.agent.planning.task_semantics_effect_paths import (
    _classify_path_roles,
    _conditional_symbolic_target,
    _file_targets,
)
from llm_agent.agent.planning.task_semantics_effect_tokens import (
    _condition_start,
    _is_negated,
    _repair_mojibake,
    _tokens,
)
from llm_agent.agent.planning.task_semantics_types import _normalize_text

_POSITIVE_SEQUENCE_FILLERS = frozenset({"apos", "after", "depois", "em", "seguida", "subsequently", "then", "entao"})


def _branch_tokens(
    clause: str,
    tokens: Sequence[str],
    effect_index: int,
    condition: str | None,
) -> tuple[tuple[str, ...], int]:
    """Return the independently governed effect clause for positive parsing."""

    if condition is None:
        branch = tuple(tokens)
        return branch, effect_index
    repaired = _repair_mojibake(clause)
    if "," in repaired:
        branch = _tokens(repaired.split(",", 1)[1])
        effect = tokens[effect_index]
        try:
            return branch, branch.index(effect)
        except ValueError:
            return (), -1
    # Keep the conditional grammar conservative when punctuation is omitted:
    # only the effect tail after the parser's selected verb can be admitted.
    return tuple(tokens[effect_index:]), 0


def _positive_prefix_supported(prefix: Sequence[str]) -> bool:
    """Accept only direct imperatives or a bounded positive request frame."""

    cleaned = tuple(prefix)
    if not cleaned:
        return True
    if any(token in _NEGATION_WORDS or token in _PROHIBITION_WORDS or token in _AMBIGUITY_WORDS for token in cleaned):
        return False
    if all(token in _IMPERATIVE_FILLERS for token in cleaned):
        return True
    # A compound direct request may introduce the next imperative after a
    # conjunction (``gere ... e salve ...``).  Only the empty tail after the
    # conjunction is accepted here; modal/prose material still has to pass
    # the governed-request rule below.
    last_conjunction = max(
        (index for index, token in enumerate(cleaned) if token in {"e", "and"}),
        default=-1,
    )
    if last_conjunction >= 0 and not cleaned[last_conjunction + 1 :]:
        return True
    # Reported requests remain advisory candidate syntax only. Canonical
    # objective authority is independently decided by the direct-user proof
    # grammar and never by this candidate projection.
    return any(token in _GOVERNED_POSITIVE_WORDS for token in cleaned)


def _positive_scope_prefix(prefix: Sequence[str]) -> tuple[str, ...]:
    """Keep only the bounded request frame governing the selected verb."""

    cleaned = tuple(prefix)
    last_conjunction = max(
        (index for index, token in enumerate(cleaned) if token in {"e", "and"}),
        default=-1,
    )
    scoped = cleaned[last_conjunction + 1 :] if last_conjunction >= 0 else cleaned
    start = 0
    while start < len(scoped) and scoped[start] in _POSITIVE_SEQUENCE_FILLERS:
        start += 1
    return scoped[start:]


def _positive_candidate_syntax(
    clause: str,
    tokens: Sequence[str],
    effect_index: int,
    target: str,
    role: PathRole,
    condition: str | None,
    path_roles: Sequence[_PathMention],
) -> bool:
    """Classify a bounded positive construction without granting authority.

    This is metadata on an advisory candidate.  The admission owner still
    checks this metadata together with source, scope, conflict, and condition
    invariants before creating durable authority.
    """

    if target == "*" or role is PathRole.UNKNOWN or _is_negated(tokens, effect_index):
        return False
    branch, branch_index = _branch_tokens(clause, tokens, effect_index, condition)
    if not branch or branch_index < 0 or branch_index >= len(branch):
        return False
    token = branch[branch_index]
    prefix = _positive_scope_prefix(branch[:branch_index])
    if any(item in _AMBIGUITY_WORDS for item in (*prefix, *branch[branch_index + 1 :])):
        return False
    if condition is None and any(item in _UNSUPPORTED_SCOPE_WORDS for item in branch):
        return False
    if role is PathRole.MEMORY:
        return token in _MEMORY_DIRECT_TERMS or token in _MEMORY_CONTEXT_TERMS
    if role not in {PathRole.DESTINATION, PathRole.MUTATION_TARGET}:
        return False
    if token == "changing":
        return False
    if token not in (_MUTATION_VERBS | _OUTPUT_VERBS | _PERSISTENCE_VERBS):
        return False
    return _positive_prefix_supported(prefix)


def _is_durable_output_verb(token: str, clause_tokens: Sequence[str], clause: str) -> bool:
    """Compatibility predicate backed by bounded path-role classification."""

    if token not in _OUTPUT_VERBS and token not in _PERSISTENCE_VERBS:
        return True
    roles = _classify_path_roles(clause, clause_tokens)
    if any(item.role in {PathRole.DESTINATION, PathRole.MUTATION_TARGET} for item in roles):
        return True
    if any(word in _EXPLICIT_TARGET_WORDS for word in clause_tokens) and not any(
        word in _OUTPUT_ONLY_NOUNS for word in clause_tokens
    ):
        # ``write the file`` is an explicit workspace operation even though
        # the caller supplied no concrete path; retain its generic effect
        # obligation without treating a source/topic path as a target.
        return True
    try:
        index = clause_tokens.index(token)
    except ValueError:
        return False
    return _conditional_symbolic_target(clause_tokens, index) is not None


def _clause_condition(clause: str) -> str | None:
    tokens = _tokens(clause)
    if not tokens:
        return None
    condition_start = _condition_start(tokens)
    if condition_start is not None or "caso contrario" in " ".join(tokens) or "otherwise" in tokens:
        start = condition_start if condition_start is not None else 0
        return " ".join(tokens[start : start + MAX_CONDITION_TOKENS])
    return None


def _predicate_claim(
    clause: str,
    previous: _PredicateClaim | None,
) -> _PredicateClaim | None:
    """Extract the bounded predicate relation governing one clause.

    Supported forms are intentionally small: a concrete path followed by a
    ``contains/contiver`` (or equality) operator and a literal, plus an
    ``otherwise/caso contrario`` branch linked to the immediately preceding
    predicate.  Anything else receives a stable unresolved identity so it can
    never grant durable authority from prose alone.
    """

    tokens = _tokens(clause)
    condition = _clause_condition(clause)
    if not tokens or condition is None:
        return None
    start = _condition_start(tokens)
    if "caso" in tokens and "contrario" in tokens or "otherwise" in tokens:
        if previous is not None:
            return _PredicateClaim(previous.predicate_id, not previous.expected, condition)
        identity = "condition:" + hashlib.sha256(_normalize_text(condition).encode("utf-8")).hexdigest()[:16]
        return _PredicateClaim(identity, False, condition)
    if start is None:
        return None
    if start >= len(tokens) or tokens[start] not in {"se", "if"}:
        return None
    operators = {
        "contiver": "contains",
        "contem": "contains",
        "contains": "contains",
        "contain": "contains",
        "conter": "contains",
        "equals": "equals",
        "equal": "equals",
        "for": "equals",
        "is": "equals",
        _COPULA_TOKEN: "equals",
    }
    operator_index = next(
        (index for index in range(start + 1, len(tokens)) if tokens[index] in operators),
        None,
    )
    paths = _file_targets(clause)
    if operator_index is not None and paths and operator_index + 1 < len(tokens):
        value_index = operator_index + 1
        # ``contiver exatamente \"literal\"`` is the Portuguese form used
        # by the deferred-condition contract.  ``exatamente``/``exactly`` is
        # a comparison modifier, not the literal being tested.
        if value_index < len(tokens) and tokens[value_index] in _DIRECT_TEXT_WORDS:
            value_index += 1
        if value_index >= len(tokens):
            value_index = operator_index + 1
        value = tokens[value_index]
        identity = f"{_normalize_text(paths[0])}|{operators[tokens[operator_index]]}|{value}"
        predicate_negated = any(
            token in _NEGATION_WORDS or token in {"not", "never"} for token in tokens[start + 1 : operator_index]
        )
        return _PredicateClaim(identity, not predicate_negated, condition)
    identity = "condition:" + hashlib.sha256(_normalize_text(condition).encode("utf-8")).hexdigest()[:16]
    return _PredicateClaim(identity, True, condition)
