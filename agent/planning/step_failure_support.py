"""Failure fact construction and terminal projections for one plan step."""

from __future__ import annotations

from typing import Any, cast

from agent.contracts import ToolArgs
from agent.planning.step_contracts import StepExecutionOutcome, StepOutcomeKind
from agent.runtime.failures import FailureFact
from agent.tools.contracts import ToolResult


def result_message(result: ToolResult, fallback: str) -> str:
    if result.error is not None:
        return result.error.message
    return result.message or fallback


def failure_from_exception(
    context: Any, index: int, tool: str, error: BaseException
) -> FailureFact:
    return FailureFact.from_exception(
        error,
        tool_name=tool,
        step_id=context.agent_state.get_step_id(index),
    )


def failure_from_result(context: Any, index: int, result: ToolResult) -> FailureFact | None:
    return FailureFact.from_tool_result(
        result,
        tool_name=context.agent_state.last_tool,
        step_id=context.agent_state.get_step_id(index),
    )


def handle_failure(
    context: Any,
    step_index: int,
    reason: str,
    tool: str,
    args: ToolArgs,
    failure: FailureFact,
) -> str:
    """Call the structured boundary with a narrow old test-port fallback."""

    handler = context._handle_step_failure
    try:
        return cast(str, handler(step_index, reason, tool, args, failure=failure))
    except TypeError as exc:
        if "failure" not in str(exc):
            raise
        return cast(str, handler(step_index, reason, tool, args))


def finish_permission_denied(
    executor: Any, index: int, result: ToolResult
) -> StepExecutionOutcome:
    reason = result_message(result, "permissão negada")
    executor.context.agent_state.mark_step_failed(index, reason)
    executor._emit_terminal("step_failed", index, reason)
    return StepExecutionOutcome(
        StepOutcomeKind.PERMISSION_DENIED,
        result=result,
        error=reason,
        final_answer=result.message or reason,
        decisive=True,
        failure=failure_from_result(executor.context, index, result),
    )


def finish_tool_failure(
    executor: Any,
    index: int,
    tool: str,
    args: ToolArgs,
    result: ToolResult,
) -> StepExecutionOutcome:
    error = result_message(result, "falha da ferramenta")
    failure = failure_from_result(executor.context, index, result)
    if failure is None:
        failure = FailureFact.unknown(
            message=error,
            tool_name=tool,
            step_id=executor.context.agent_state.get_step_id(index),
        )
    action = handle_failure(
        executor.context,
        index + 1,
        f"Tool '{tool}' falhou: {error}",
        tool,
        args,
        failure,
    )
    if action == "replan":
        executor.finish_failed(index, error, result, failure=failure)
        return StepExecutionOutcome(
            StepOutcomeKind.REPLAN,
            result=result,
            error=error,
            failure=failure,
        )
    if action == "continue":
        executor.context._purge_stale_context()
    else:
        executor.context.fail_task()
    return cast(
        StepExecutionOutcome,
        executor.finish_failed(
            index, error, result, decisive=action != "continue", failure=failure
        ),
    )


def finish_post_process_failure(
    executor: Any,
    index: int,
    tool: str,
    args: ToolArgs,
    result: ToolResult,
) -> StepExecutionOutcome:
    error = result_message(result, "falha no pós-processamento")
    failure = FailureFact.from_code(
        "EXECUTION_ERROR",
        message=error,
        tool_name=tool,
        step_id=executor.context.agent_state.get_step_id(index),
    )
    action = handle_failure(
        executor.context,
        index + 1,
        f"Tool '{tool}' falhou: {error}",
        tool,
        args,
        failure,
    )
    if action == "replan":
        executor.finish_failed(index, error, result, failure=failure)
        return StepExecutionOutcome(
            StepOutcomeKind.REPLAN,
            result=result,
            error=error,
            failure=failure,
        )
    return cast(
        StepExecutionOutcome,
        executor.finish_failed(
            index, error, result, decisive=action != "continue", failure=failure
        ),
    )


__all__ = [
    "failure_from_exception",
    "failure_from_result",
    "finish_permission_denied",
    "finish_post_process_failure",
    "finish_tool_failure",
    "handle_failure",
    "result_message",
]
