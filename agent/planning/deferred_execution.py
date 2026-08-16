"""Execution helpers for the closed deferred-condition primitive."""

from __future__ import annotations

from typing import Any, cast

from agent.contracts import ToolResult
from agent.execution_state import StepStatus
from agent.planning.deferred_condition import (
    evaluate_equals,
    validate_deferred_condition,
)
from agent.planning.execution_models import StepLoopResult
from agent.planning.task_completion import bind_effect_waiver
from agent.state_progression import current_result_for_step


def execute_deferred_condition(
    executor: Any,
    index: int,
    objective: str,
) -> StepLoopResult:
    state = executor.orchestrator.agent_state
    step = state.plan[index]
    problem = validate_deferred_condition(step, index, state.plan, objective)
    if problem:
        return block_deferred(executor, index, problem)
    state.mark_step_running(index)

    reference = step.get("observation_ref")
    resolved = _resolve_observation(executor, index, reference)
    if isinstance(resolved, str):
        return block_deferred(executor, index, resolved)
    history_index, result = resolved

    predicate = step["predicate"]
    try:
        matched = evaluate_equals(result.get("data"), str(predicate["value"]))
    except ValueError as exc:
        return block_deferred(executor, index, str(exc))

    if matched:
        state.mark_step_completed(index)
        state.insert_plan_step(index + 1, cast(dict[str, Any], step["on_true"]))
        executor._rebuild_dependency_map()
        emit_deferred_resolution(executor, index, reference, "true")
        return StepLoopResult(index + 1)

    if not bind_effect_waiver(
        executor.orchestrator,
        history_index,
        effects=("write",),
        source="deferred_condition",
    ):
        return block_deferred(executor, index, "waiver canônica não pôde ser vinculada")
    state.mark_step_completed(index)
    emit_deferred_resolution(executor, index, reference, "false")
    return StepLoopResult(index + 1)


def _resolve_observation(
    executor: Any,
    index: int,
    reference: Any,
) -> tuple[int, ToolResult] | str:
    state = executor.orchestrator.agent_state
    if not isinstance(reference, str) or not reference:
        return "observation_ref não foi vinculada à identidade canônica"
    observation_index = next(
        (candidate for candidate in range(index) if state.get_step_id(candidate) == reference),
        -1,
    )
    if observation_index < 0:
        return "identidade canônica da observação não existe no plano"
    if state.get_step_status(observation_index) is not StepStatus.COMPLETED:
        return "a observação referenciada não foi concluída com sucesso"
    history_match = current_result_for_step(state.tool_history, reference)
    if history_match is None:
        return "resultado canônico da observação indisponível"
    history_index, history_item = history_match
    result = history_item.get("result")
    if not isinstance(result, dict) or (
        result.get("executed") is not True
        or result.get("status") != "succeeded"
        or "data" not in result
    ):
        return "observação canônica não elegível"
    if not has_complete_text_observation(cast(ToolResult, result)):
        return "observação textual integral não está disponível"
    return history_index, cast(ToolResult, result)


def has_complete_text_observation(result: ToolResult) -> bool:
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, (list, tuple)):
        return False
    return any(
        isinstance(item, dict)
        and item.get("kind") == "text_observation"
        and isinstance(item.get("metadata"), dict)
        and item["metadata"].get("complete") is True
        for item in artifacts
    )


def emit_deferred_resolution(
    executor: Any,
    index: int,
    observation_step_id: str,
    selected_branch: str,
) -> None:
    executor.orchestrator._emit(
        "deferred_condition_resolved",
        {
            "step": index + 1,
            "step_id": executor.orchestrator.agent_state.get_step_id(index),
            "observation_step_id": observation_step_id,
            "operator": "equals",
            "selected_branch": selected_branch,
        },
    )


def block_deferred(executor: Any, index: int, reason: str) -> StepLoopResult:
    message = f"Condição deferred bloqueada: {reason}."
    result: ToolResult = {
        "ok": False,
        "done": True,
        "status": "blocked",
        "executed": False,
        "error": "deferred_condition_blocked",
        "message": message,
    }
    state = executor.orchestrator.agent_state
    state.mark_step_blocked(index, reason)
    state.project_last_result("planner", {}, result)
    executor.orchestrator._emit(
        "deferred_condition_blocked",
        {
            "step": index + 1,
            "step_id": state.get_step_id(index),
            "reason": reason,
        },
    )
    return StepLoopResult(index, result=result, answer=message, stop=True)
