"""Legacy response shapes kept outside canonical admission."""

from __future__ import annotations

from typing import Any

from agent.llm.decision_contract import (
    ModelRequestContract,
    _is_string,
    _valid_effect_observation_continuation,
    _valid_plan_action,
    _valid_plan_items,
    _valid_reactive_tool_decision,
    resolve_request_contract,
)


def _compat_initial(value: dict[str, Any]) -> dict[str, Any] | None:
    if set(value) == {"plan"} and _valid_plan_items(value["plan"]):
        return dict(value)
    if _valid_plan_action(value, "continue_after_plan", obligations=True):
        return dict(value)
    return dict(value) if value == {"action": "replan"} else None


def _compat_effect(value: dict[str, Any]) -> dict[str, Any] | None:
    if _valid_plan_action(value, "continue_after_plan", obligations=False):
        return dict(value)
    candidate = dict(value)
    candidate.pop("answer", None)
    if (
        set(value) == {"action", "observation_index", "answer"}
        and _valid_effect_observation_continuation(candidate)
    ):
        return dict(value)
    return None


def legacy_model_decision_compatibility(
    value: Any,
    step_type: str | None = None,
    *,
    request_contract: Any = None,
) -> dict[str, Any] | None:
    """Return only explicitly identified historical, non-canonical shapes."""

    if not isinstance(value, dict):
        return None
    contract = resolve_request_contract(request_contract=request_contract, step_type=step_type)
    if contract is ModelRequestContract.REACTIVE_TOOL_DECISION and value == {"action": "final"}:
        return dict(value)
    if contract is ModelRequestContract.REPLAN and "action" not in value:
        candidate = {"action": "tool", **value}
        return candidate if _valid_reactive_tool_decision(candidate) else None
    if contract is ModelRequestContract.INITIAL_PLAN:
        return _compat_initial(value)
    if contract is ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION:
        return _compat_effect(value)
    if contract is ModelRequestContract.FINAL_GENERATION and (
        set(value) == {"action", "answer"}
        and value.get("action") == "final"
        and _is_string(value["answer"])
    ):
        return dict(value)
    return None
