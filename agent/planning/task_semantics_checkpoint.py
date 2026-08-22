"""Checkpoint projection and restoration for canonical task semantics."""

from __future__ import annotations

from typing import Any, Mapping

from agent.planning.task_semantics_types import (
    TaskIntent,
    TaskObligation,
    TaskSemanticsError,
    _normalize_effect,
    validate_closed_obligation,
)

TASK_SEMANTICS_SCHEMA_VERSION = 2


def snapshot(owner: Any) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            **item.to_dict(),
            "status": owner._statuses[item.id].value,
            "evidence_refs": list(owner._evidence[item.id]),
        }
        for item in owner._obligations
    )


def to_checkpoint_dict(owner: Any) -> dict[str, Any]:
    return {
        "schema_version": TASK_SEMANTICS_SCHEMA_VERSION,
        "objective": owner.objective,
        "requested_effects": list(owner.requested_effects),
        "prohibited_effects": list(owner.prohibited_effects),
        "executed_effects": list(owner.executed_effects()),
        "waived_effects": list(owner.waived_effects()),
        "obligations": [item.to_dict() for item in owner._obligations],
        "statuses": {key: value.value for key, value in owner._statuses.items()},
        "evidence": {key: list(value) for key, value in owner._evidence.items() if value},
    }


def restore_from_checkpoint(cls: Any, data: Mapping[str, Any]) -> Any:
    if not isinstance(data, Mapping):
        raise TaskSemanticsError("task semantics ausente ou invalido")
    objective = data.get("objective")
    raw_obligations = data.get("obligations")
    if data.get("schema_version") != TASK_SEMANTICS_SCHEMA_VERSION:
        raise TaskSemanticsError("checkpoint de task semantics sem versao inequívoca")
    if not isinstance(objective, str) or not isinstance(raw_obligations, list):
        raise TaskSemanticsError("checkpoint sem contrato semantico valido")
    definitions = [_obligation_from_checkpoint(raw) for raw in raw_obligations]
    requested = tuple(dict.fromkeys(_normalize_effect(item) for item in (data.get("requested_effects") or [])))
    prohibited = tuple(dict.fromkeys(_normalize_effect(item) for item in (data.get("prohibited_effects") or [])))
    return cls(
        TaskIntent(objective, requested, prohibited),
        definitions,
        statuses=data.get("statuses"),
        evidence=data.get("evidence"),
        # These projections are derived from terminal evidence after restore;
        # serialized lists are not independent authority.
        executed_effects=(),
        waived_effects=(),
        _strict_evidence=True,
    )


def _obligation_from_checkpoint(raw: Any) -> TaskObligation:
    if not isinstance(raw, Mapping):
        raise TaskSemanticsError("checkpoint contem obrigacao invalida")
    identifier = raw.get("id")
    kind = raw.get("kind")
    description = raw.get("description")
    if not isinstance(identifier, str) or not isinstance(kind, str) or not isinstance(description, str):
        raise TaskSemanticsError("checkpoint contem obrigacao incompleta")
    effect = raw.get("effect")
    condition = raw.get("condition")
    if effect is not None and not isinstance(effect, str):
        raise TaskSemanticsError("checkpoint contem efeito de obrigacao invalido")
    if condition is not None and not isinstance(condition, str):
        raise TaskSemanticsError("checkpoint contem condition de obrigacao invalida")
    obligation = TaskObligation(
        id=identifier,
        kind=kind,
        description=description,
        effect=effect,
        condition=condition,
        target=raw.get("target"),
        query=raw.get("query"),
        operands=raw.get("operands", ()),
        fallback_target=raw.get("fallback_target"),
        query_source=raw.get("query_source"),
    )
    validate_closed_obligation(obligation)
    return obligation


__all__ = ["TASK_SEMANTICS_SCHEMA_VERSION", "snapshot", "to_checkpoint_dict", "restore_from_checkpoint"]
