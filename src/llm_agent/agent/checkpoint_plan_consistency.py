"""Pure structural checks shared by checkpoint validation and projection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def plan_consistency_error(
    plan: Any,
    records: Any,
    plan_step: Any,
    current_step_id: Any,
) -> str | None:
    """Return a bounded structural error for plan/record cross-fields."""

    if not isinstance(plan, list) or any(not isinstance(item, Mapping) for item in plan):
        return "checkpoint com plano estruturalmente invalido"
    plan_ids = _identities(plan, "_step_id")
    if plan_ids is None:
        return "checkpoint com identidade de passo invalida"
    if len(plan_ids) != len(set(plan_ids)):
        return "checkpoint com identidade de passo duplicada"
    if not isinstance(records, list) or any(not isinstance(item, Mapping) for item in records):
        return "checkpoint sem registros de execucao validos"
    record_ids = _identities(records, "step_id")
    if record_ids is None:
        return "checkpoint com identidade de registro invalida"
    if len(record_ids) != len(set(record_ids)):
        return "checkpoint com registro de passo duplicado"
    if set(plan_ids) != set(record_ids):
        return "checkpoint com plano e registros de passos inconsistentes"
    if isinstance(plan_step, bool) or not isinstance(plan_step, int) or not 0 <= plan_step <= len(plan_ids):
        return "cursor de plano inconsistente com o plano"
    if current_step_id is not None and (
        not isinstance(current_step_id, str)
        or not current_step_id.strip()
        or current_step_id not in plan_ids
    ):
        return "passo atual inconsistente com o plano"
    return None


def _identities(items: list[Mapping[str, Any]], field: str) -> list[str] | None:
    identities: list[str] = []
    for item in items:
        value = item.get(field)
        if not isinstance(value, str) or not value.strip():
            return None
        identities.append(value)
    return identities


__all__ = ["plan_consistency_error"]
