"""Bounded canonical review of model-proposed task obligations."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from agent.planning.task_semantics_types import (
    MAX_OBLIGATIONS,
    MAX_REVIEW_OBLIGATIONS,
    ObligationStatus,
    TaskObligation,
    TaskSemanticsError,
    _normalize_text,
    validate_closed_obligation,
)

_OBLIGATION_KEYS = frozenset(
    {
        "id",
        "kind",
        "description",
        "effect",
        "condition",
        "target",
        "query",
        "operands",
        "fallback_target",
        "query_source",
    }
)
_FORBIDDEN_KEYS = frozenset(
    {"status", "terminal", "success", "succeeded", "completed", "satisfied", "waived", "blocked", "result", "data", "tool", "instructions"}
)


def review_and_add(owner: Any, raw: Any, *, source: str) -> tuple[TaskObligation, ...]:
    if source not in {"initial_plan", "canonical_review"}:
        raise TaskSemanticsError("obrigacoes so podem entrar por revisao canonica")
    if not isinstance(raw, (list, tuple)) or len(raw) > MAX_REVIEW_OBLIGATIONS:
        raise TaskSemanticsError("payload de obrigacoes invalido ou ilimitado")
    candidates: list[TaskObligation] = []
    seen = set(owner._statuses)
    existing_identities = {_semantic_identity(item) for item in owner._obligations}
    for item in raw:
        candidate = _candidate(item, owner, seen)
        identity = _semantic_identity(candidate)
        if identity in existing_identities:
            raise TaskSemanticsError("obrigacao equivalente ja existe")
        existing_identities.add(identity)
        candidates.append(candidate)
    if len(owner._obligations) + len(candidates) > MAX_OBLIGATIONS:
        raise TaskSemanticsError("limite de obrigacoes excedido")
    owner._obligations = owner._obligations + tuple(candidates)
    for item in candidates:
        owner._statuses[item.id] = ObligationStatus.PENDING
        owner._evidence[item.id] = []
    return tuple(candidates)


def _semantic_identity(item: TaskObligation) -> tuple[Any, ...]:
    """Identify a bounded requirement independently of model prose or id."""

    if item.kind == "effect":
        return (item.kind, item.effect)
    if item.kind == "read":
        return (item.kind, item.target)
    if item.kind == "search":
        return (item.kind, item.query, item.query_source)
    if item.kind == "compare":
        return (item.kind, tuple(sorted(item.operands, key=str.casefold)))
    if item.kind == "analyze":
        return (item.kind, item.target, item.query)
    if item.kind == "fallback":
        return (item.kind, item.fallback_target)
    return (item.kind,)


def _candidate(item: Any, owner: Any, seen: set[str]) -> TaskObligation:
    if not isinstance(item, Mapping):
        raise TaskSemanticsError("obrigacao de modelo deve ser objeto")
    keys = set(item)
    if keys & _FORBIDDEN_KEYS or not keys.issubset(_OBLIGATION_KEYS):
        raise TaskSemanticsError("payload de obrigacao contem autoridade proibida")
    kind = item.get("kind")
    description = item.get("description")
    if not isinstance(kind, str) or not isinstance(description, str):
        raise TaskSemanticsError("obrigacao requer kind e description")
    effect = item.get("effect")
    if kind.casefold() == "effect" and effect not in owner.requested_effects:
        raise TaskSemanticsError("modelo nao pode inventar efeito solicitado")
    identifier = item.get("id")
    if identifier is None:
        identifier = _stable_id(
            kind,
            description,
            effect if isinstance(effect, str) else None,
            item,
        )
    obligation = TaskObligation(
        id=identifier,
        kind=kind,
        description=description,
        effect=effect,
        condition=item.get("condition"),
        target=item.get("target"),
        query=item.get("query"),
        operands=item.get("operands", ()),
        fallback_target=item.get("fallback_target"),
        query_source=item.get("query_source"),
    )
    validate_closed_obligation(obligation)
    if obligation.id in seen:
        raise TaskSemanticsError("ids de obrigacoes duplicados")
    seen.add(obligation.id)
    return obligation


def _stable_id(kind: str, description: str, effect: str | None, item: Mapping[str, Any]) -> str:
    identity = "|".join(
        str(item.get(key, ""))
        for key in ("target", "query", "operands", "fallback_target", "query_source")
    )
    material = f"{kind}|{effect or ''}|{identity}|{_normalize_text(description)}"
    return f"requirement:{kind}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}"
