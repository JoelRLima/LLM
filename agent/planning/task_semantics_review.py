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
)

_OBLIGATION_KEYS = frozenset({"id", "kind", "description", "effect", "condition"})
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
    for item in raw:
        candidates.append(_candidate(item, owner, seen))
    if len(owner._obligations) + len(candidates) > MAX_OBLIGATIONS:
        raise TaskSemanticsError("limite de obrigacoes excedido")
    owner._obligations = owner._obligations + tuple(candidates)
    for item in candidates:
        owner._statuses[item.id] = ObligationStatus.PENDING
        owner._evidence[item.id] = []
    return tuple(candidates)


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
        identifier = _stable_id(kind, description, effect if isinstance(effect, str) else None)
    obligation = TaskObligation(
        id=identifier,
        kind=kind,
        description=description,
        effect=effect,
        condition=item.get("condition"),
    )
    if obligation.id in seen:
        raise TaskSemanticsError("ids de obrigacoes duplicados")
    seen.add(obligation.id)
    return obligation


def _stable_id(kind: str, description: str, effect: str | None) -> str:
    material = f"{kind}|{effect or ''}|{_normalize_text(description)}"
    return f"requirement:{kind}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}"
