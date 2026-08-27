"""Prepare and dispatch one read batch through the canonical step path."""

from __future__ import annotations

import concurrent.futures
import uuid
from dataclasses import replace
from typing import Any, Dict, List

from agent.planning.parallel_contracts import (
    ParallelInvocation,
    correlate_parallel_result,
    future_parallel_result,
)
from agent.planning.step_executor import StepExecutionOutcome
from agent.runtime.limits import runtime_limit_values
from agent.tools.contracts import ToolError, ToolInvocationRequest, ToolResult, ToolStatus


def run_parallel_tools(
    plan_executor: Any,
    indices: List[int],
) -> tuple[Dict[int, ToolResult], Dict[int, ToolResult], Dict[int, ParallelInvocation]]:
    """Prepare every slot before dispatching any adapter invocation."""

    state = plan_executor.orchestrator.agent_state
    cached: Dict[int, ToolResult] = {}
    results: Dict[int, ToolResult] = {}
    correlations: Dict[int, ParallelInvocation] = {}
    futures: Dict[concurrent.futures.Future[ToolResult], int] = {}
    limits = runtime_limit_values(plan_executor.orchestrator.session.config)
    workers = min(limits["max_io_concurrency"], len(indices))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for index in indices:
            prepared = plan_executor.step_executor.prepare_invocation(index)
            if isinstance(prepared, StepExecutionOutcome):
                tool, args, _ = plan_executor._step_data(index)
                invocation_id = str(uuid.uuid4())
                correlations[index] = ParallelInvocation(
                    index=index,
                    step_id=state.get_step_id(index),
                    invocation_id=invocation_id,
                    request=ToolInvocationRequest(invocation_id, tool, args),
                )
                cached[index] = ToolResult(
                    invocation_id=invocation_id,
                    status=ToolStatus.FAILED,
                    error=ToolError(
                        "PREPARATION_FAILED",
                        prepared.error or "preparacao da invocacao falhou",
                    ),
                    executed=False,
                )
                continue
            tool, args, file_path = prepared.tool, prepared.args, prepared.file_path
            invocation_id = str(uuid.uuid4())
            prepared_for_dispatch = replace(prepared, invocation_id=invocation_id)
            correlation = ParallelInvocation(
                index=index,
                step_id=state.get_step_id(index),
                invocation_id=invocation_id,
                request=ToolInvocationRequest(
                    invocation_id, tool, prepared_for_dispatch.args
                ),
                prepared=prepared_for_dispatch,
            )
            correlations[index] = correlation
            state.mark_step_running(index)
            cache_hit, cache_result = plan_executor.step_executor.try_cache(
                tool, args, file_path, correlation.step_id, record_result=False
            )
            if cache_hit and cache_result is not None:
                cached[index] = correlate_parallel_result(cache_result, correlation)
                continue
            if not getattr(plan_executor.orchestrator, "tool_invocation_gateway", None):
                plan_executor.orchestrator._emit("tool_start", {"tool": tool, "args": args})
            runner = getattr(plan_executor.orchestrator.tool_executor, "run_tool_invocation", None)
            canonical_prepared_runner = getattr(
                plan_executor.orchestrator.tool_executor,
                "run_prepared_invocation_canonical",
                None,
            )
            prepared_runner = getattr(
                plan_executor.orchestrator.tool_executor,
                "run_prepared_invocation",
                None,
            )
            if callable(canonical_prepared_runner):
                future = pool.submit(
                    canonical_prepared_runner, prepared_for_dispatch, False
                )
            elif callable(prepared_runner):
                future = pool.submit(
                    prepared_runner, prepared_for_dispatch, False
                )
            elif callable(runner):
                future = pool.submit(runner, correlation.request, False)
            else:
                future = pool.submit(
                    plan_executor.orchestrator.tool_executor.run_tool, tool, args, False
                )
            futures[future] = index
        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            results[index] = future_parallel_result(future, correlations[index])
    return cached, results, correlations
