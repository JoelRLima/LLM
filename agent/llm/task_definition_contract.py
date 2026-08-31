"""Exact admission validators for task-definition model responses."""

from __future__ import annotations

from typing import Any

from agent.task_definition.models import TaskContract, TaskSpec
from agent.task_definition.serialization import serialize_contract, serialize_spec


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def valid_task_contract_decision(decision: dict[str, Any]) -> bool:
    action = decision.get("action")
    if action == "define_contract":
        if set(decision) != {"action", "contract"}:
            return False
        try:
            contract = TaskContract.from_dict(decision["contract"])
            serialize_contract(contract)
        except (TypeError, ValueError):
            return False
        return True
    if action == "needs_input":
        return (
            set(decision) == {"action", "reason", "question"}
            and _is_non_empty_string(decision["reason"])
            and _is_non_empty_string(decision["question"])
        )
    return (
        action == "blocked"
        and set(decision) == {"action", "reason"}
        and _is_non_empty_string(decision["reason"])
    )


def valid_task_spec_decision(decision: dict[str, Any]) -> bool:
    action = decision.get("action")
    if action == "define_spec":
        if set(decision) != {"action", "spec"}:
            return False
        try:
            spec = TaskSpec.from_dict(decision["spec"])
            serialize_spec(spec)
        except (TypeError, ValueError):
            return False
        return True
    return (
        action == "blocked"
        and set(decision) == {"action", "reason"}
        and _is_non_empty_string(decision["reason"])
    )
