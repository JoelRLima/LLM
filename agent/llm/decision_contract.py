"""Closed model-response contracts and their fail-closed admission rules."""
from __future__ import annotations

import re
from collections.abc import Callable
from enum import Enum
from typing import Any, TypeGuard, cast

from agent.llm.task_definition_contract import (
    valid_task_contract_decision as _valid_task_contract_decision,
)
from agent.llm.task_definition_contract import (
    valid_task_spec_decision as _valid_task_spec_decision,
)
from agent.llm.tool_discovery_contract import valid_tool_discovery as _valid_tool_discovery


class ModelRequestContract(str, Enum):
    INTERACTION_RESOLUTION = "interaction_resolution"
    TASK_CONTRACT = 'task_contract'
    TASK_SPEC = 'task_spec'
    INITIAL_PLAN = "initial_plan"
    EFFECT_OBSERVATION_CONTINUATION = "effect_observation_continuation"
    REASONING_BOUNDARY_CONTINUATION = "reasoning_boundary_continuation"
    MACRO_PLAN = "macro_plan"
    REACTIVE_TOOL_DECISION = "reactive_tool_decision"
    FINAL_GENERATION = "final_generation"
    SUMMARIZATION = "summarization"
    REPLAN = "replan"
    TOOL_DISCOVERY = "tool_discovery"
    INITIAL_PLANNING = "initial_plan"
    EFFECT_CONTINUATION = "effect_observation_continuation"
    REASONING_CONTINUATION = "reasoning_boundary_continuation"
    MACRO_PLANNING = "macro_plan"
    REACTIVE_TOOL = "reactive_tool_decision"
    FINAL = "final_generation"
    SUMMARIZE = "summarization"
RequestContract = ModelRequestContract
RequestContractId = ModelRequestContract
_STEP_TYPE_CONTRACTS: dict[str, ModelRequestContract] = {
    'task_contract': ModelRequestContract.TASK_CONTRACT,
    'task_spec': ModelRequestContract.TASK_SPEC,
    "plan": ModelRequestContract.INITIAL_PLAN,
    "macro_plan": ModelRequestContract.MACRO_PLAN,
    "tool_decision": ModelRequestContract.REACTIVE_TOOL_DECISION,
    "final": ModelRequestContract.FINAL_GENERATION,
    "summarize": ModelRequestContract.SUMMARIZATION,
    "replan": ModelRequestContract.REPLAN,
    "tool_discovery": ModelRequestContract.TOOL_DISCOVERY,
}
def coerce_request_contract(value: Any) -> ModelRequestContract | None:
    if isinstance(value, ModelRequestContract):
        return value
    if not isinstance(value, str):
        return None
    try:
        return ModelRequestContract(value)
    except ValueError:
        return None
def request_contract_value(value: Any) -> str | None:
    contract = coerce_request_contract(value)
    return contract.value if contract is not None else None
def request_contract_for_step_type(step_type: str | None) -> ModelRequestContract | None:
    """Return an exact contract only where the public step type is unambiguous."""
    return _STEP_TYPE_CONTRACTS.get(step_type) if isinstance(step_type, str) else None
def resolve_request_contract(
    *, request_contract: Any = None, step_type: str | None = None
) -> ModelRequestContract | None:
    explicit = coerce_request_contract(request_contract)
    if request_contract is not None and explicit is None:
        return None
    grouped = request_contract_for_step_type(step_type)
    if explicit is not None and grouped is not None and explicit is not grouped:
        return None
    return explicit or grouped
def request_contract_for_request(
    request: Any, *, request_contract: Any = None, step_type: str | None = None
) -> ModelRequestContract | None:
    """Resolve carried request identity without inferring it from a response."""
    raw_carried = getattr(request, "request_contract", None)
    carried = coerce_request_contract(raw_carried)
    if raw_carried is not None and carried is None:
        return None
    supplied = resolve_request_contract(
        request_contract=request_contract, step_type=step_type
    )
    if carried is not None and supplied is not None and carried is not supplied:
        return None
    return carried or supplied
def _is_string(value: Any) -> bool:
    return isinstance(value, str)
def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
def _is_integer(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)
_MAX_PATH_INDEX = 1_000_000
_MAX_PATH_LENGTH = 32
_MAX_PATH_KEY = 128
_BINDING_TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
def _valid_path_segment(value: Any) -> bool:
    if _is_integer(value):
        return 0 <= value <= _MAX_PATH_INDEX
    return bool(
        isinstance(value, str)
        and 0 < len(value) <= _MAX_PATH_KEY
        and not value.startswith("__")
        and all(ord(character) >= 32 for character in value)
    )
def _valid_bindings(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for target, binding in value.items():
        if not isinstance(target, str) or not _BINDING_TARGET.fullmatch(target):
            return False
        if not isinstance(binding, dict) or set(binding) != {"from_step", "path"}:
            return False
        if not _is_integer(binding["from_step"]) or binding["from_step"] < 1:
            return False
        path = binding["path"]
        if not isinstance(path, list) or len(path) > _MAX_PATH_LENGTH:
            return False
        if not all(_valid_path_segment(segment) for segment in path):
            return False
    return True
def _valid_tool_step(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) - {"tool", "args", "bindings"}:
        return False
    if not _is_non_empty_string(value.get("tool")) or not isinstance(
        value.get("args"), dict
    ):
        return False
    return "bindings" not in value or _valid_bindings(value["bindings"])
def _valid_deferred_condition(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = {"kind", "observation_ref", "predicate", "on_true", "on_false"}
    if set(value) != required or value["kind"] != "deferred_condition":
        return False
    if not _is_integer(value["observation_ref"]) or value["observation_ref"] < 1:
        return False
    predicate = value["predicate"]
    if (
        not isinstance(predicate, dict)
        or set(predicate) != {"op", "value"}
        or predicate["op"] != "equals"
        or not _is_string(predicate["value"])
    ):
        return False
    return value["on_false"] == {"waive_effect": "write"} and _valid_tool_step(
        value["on_true"]
    )
def _valid_plan_items(value: Any) -> bool:
    return isinstance(value, list) and all(
        _valid_deferred_condition(item) or _valid_tool_step(item) for item in value
    )
def _valid_plan_action(
    decision: dict[str, Any],
    action: str,
    *,
    obligations: bool,
    require_non_empty_plan: bool = False,
) -> bool:
    allowed = {"action", "plan"} | ({"obligations"} if obligations else set())
    if set(decision) - allowed or decision.get("action") != action:
        return False
    plan = decision.get("plan")
    if not _valid_plan_items(plan) or (require_non_empty_plan and not plan):
        return False
    return not obligations or "obligations" not in decision or isinstance(
        decision["obligations"], list
    )
def _valid_initial_plan(decision: dict[str, Any]) -> bool:
    if _valid_plan_action(decision, "use_tools", obligations=True):
        return True
    return set(decision) == {"action", "answer"} and decision.get(
        "action"
    ) == "direct_response" and _is_string(decision["answer"])
def _valid_macro_step(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required = {"id", "title", "goal", "priority"}
    optional = {"depends_on", "estimated_tools"}
    if set(value) - required - optional or not required <= set(value):
        return False
    if not all(_is_non_empty_string(value[key]) for key in required):
        return False
    return all(
        isinstance(value[key], list) and all(_is_string(item) for item in value[key])
        for key in optional
        if key in value
    )
def _valid_macro_plan(decision: dict[str, Any]) -> bool:
    return set(decision) == {"steps"} and isinstance(decision["steps"], list) and all(
        _valid_macro_step(step) for step in decision["steps"]
    )
def _valid_reactive_tool_decision(decision: dict[str, Any]) -> bool:
    action = decision.get("action")
    if action == "final":
        return set(decision) == {"action", "answer"} and _is_string(decision["answer"])
    if action != "tool" or set(decision) - {"action", "tool", "args", "bindings"}:
        return False
    return _is_non_empty_string(decision.get("tool")) and isinstance(
        decision.get("args"), dict
    ) and ("bindings" not in decision or _valid_bindings(decision["bindings"]))
def _valid_effect_observation_continuation(decision: dict[str, Any]) -> bool:
    action = decision.get("action")
    if action == "execute":
        return _valid_plan_action(
            decision, "execute", obligations=False, require_non_empty_plan=True
        )
    if action == "complete_without_effect":
        return set(decision) == {"action", "observation_index"} and _is_integer(
            decision["observation_index"]
        ) and decision["observation_index"] >= 1
    return action == "blocked" and set(decision) == {"action", "reason"} and _is_non_empty_string(
        decision["reason"]
    )
def _valid_reasoning_boundary_continuation(decision: dict[str, Any]) -> bool:
    action = decision.get("action")
    if action == "execute":
        return _valid_plan_action(
            decision, "execute", obligations=True, require_non_empty_plan=True
        )
    if action == "complete":
        return (
            {"action", "reason"} <= set(decision) <= {"action", "reason", "obligations"}
            and _is_non_empty_string(decision["reason"])
            and (
                "obligations" not in decision or isinstance(decision["obligations"], list)
            )
        )
    return action == "blocked" and set(decision) == {"action", "reason"} and _is_non_empty_string(
        decision.get("reason")
    )
def _valid_final_generation(decision: dict[str, Any]) -> bool:
    return set(decision) == {"answer"} and _is_string(decision["answer"])
def _valid_summarize(decision: dict[str, Any]) -> bool:
    return set(decision) == {"summary"} and _is_string(decision["summary"])
def _valid_replan(decision: dict[str, Any]) -> bool:
    return decision.get("action") == "tool" and _valid_reactive_tool_decision(decision)

_CONTRACT_VALIDATORS: dict[ModelRequestContract, Callable[[dict[str, Any]], bool]] = {
    ModelRequestContract.TASK_CONTRACT: _valid_task_contract_decision,
    ModelRequestContract.TASK_SPEC: _valid_task_spec_decision,
    ModelRequestContract.INITIAL_PLAN: _valid_initial_plan,
    ModelRequestContract.MACRO_PLAN: _valid_macro_plan,
    ModelRequestContract.REACTIVE_TOOL_DECISION: _valid_reactive_tool_decision,
    ModelRequestContract.FINAL_GENERATION: _valid_final_generation,
    ModelRequestContract.SUMMARIZATION: _valid_summarize,
    ModelRequestContract.EFFECT_OBSERVATION_CONTINUATION: _valid_effect_observation_continuation,
    ModelRequestContract.REASONING_BOUNDARY_CONTINUATION: _valid_reasoning_boundary_continuation,
    ModelRequestContract.REPLAN: _valid_replan,
    ModelRequestContract.TOOL_DISCOVERY: _valid_tool_discovery,
}
def normalize_generic_model_decision(value: Any) -> dict[str, Any] | None:
    """Keep the historical object normalization outside canonical admission."""
    if not isinstance(value, dict):
        return None
    return {"action": "tool", **value} if "action" not in value and "tool" in value else dict(value)
def legacy_model_decision_compatibility(
    value: Any,
    step_type: str | None = None,
    *,
    request_contract: Any = None,
) -> dict[str, Any] | None:
    from agent.llm.task_definition_decision_compat import (
        legacy_model_decision_compatibility as compatibility,
    )

    return cast(
        dict[str, Any] | None,
        compatibility(
            value,
            step_type=step_type,
            request_contract=request_contract,
        ),
    )
def admit_model_decision_value(
    value: Any,
    step_type: str | None = None,
    *,
    request_contract: Any = None,
) -> dict[str, Any] | None:
    """Admit a parsed value only under its exact expected request contract."""
    if not isinstance(value, dict):
        return None
    contract = resolve_request_contract(request_contract=request_contract, step_type=step_type)
    validator = _CONTRACT_VALIDATORS.get(contract) if contract is not None else None
    return dict(value) if validator is not None and validator(value) else None
__all__ = ["ModelRequestContract", "RequestContract", "RequestContractId", "admit_model_decision_value", "coerce_request_contract", "legacy_model_decision_compatibility", "normalize_generic_model_decision", "request_contract_for_request", "request_contract_for_step_type", "request_contract_value", "resolve_request_contract"]
