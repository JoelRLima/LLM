"""Finalization ownership for one slot of a parallel plan batch."""

from __future__ import annotations

from typing import Any, Dict

from agent.contracts import ToolResult
from agent.planning.parallel_contracts import ParallelInvocation, correlate_parallel_result


def finalize_parallel_index(
    executor: Any,
    index: int,
    cached: Dict[int, ToolResult],
    results: Dict[int, ToolResult],
    correlations: Dict[int, ParallelInvocation],
    objective: str,
    usage: Dict[str, int],
) -> tuple[Any, ToolResult]:
    """Record before optional summarization, then apply normal step policy."""

    state = executor.orchestrator.agent_state
    tool = correlations[index].request.tool_name
    args = dict(correlations[index].request.arguments)
    file_path = str(args.get("target") or args.get("file_path") or "")
    result = cached.get(index) or results.get(
        index, {"ok": False, "done": False, "data": None, "error": "Falha desconhecida"}
    )
    result = correlate_parallel_result(result, correlations[index])
    if not getattr(executor.orchestrator, "tool_invocation_gateway", None) or index in cached:
        executor.orchestrator._emit(
            "tool_end",
            {"tool": tool, "ok": result.get("ok"), "invocation_id": result.get("invocation_id")},
        )
    state.record_tool_result(
        tool, args, result, step_id=correlations[index].step_id, logical_slot=index
    )
    try:
        executor.orchestrator._maybe_summarize_and_store(tool, args, result)
    except Exception as exc:
        executor.orchestrator._emit(
            "warning", {"step": index + 1, "warning": f"Falha ao resumir resultado: {exc}"}
        )
    return executor.step_executor.finalize_result(
        index, tool, args, result, file_path, objective, usage
    ), result
