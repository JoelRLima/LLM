import concurrent.futures
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, cast

from agent.contracts import ToolArgs, ToolResult
from agent.cost_guard import DEFAULT_MAX_TASK_TOOL_CALLS, CostGuard
from agent.planning.parallel_contracts import (
    ParallelInvocation,
    correlate_parallel_result,
    future_parallel_result,
)
from agent.planning.parallel_finalizer import finalize_parallel_index
from agent.planning.replan import ReplanContext, replan
from agent.planning.step_executor import StepExecutor, StepOutcomeKind
from agent.tools.contracts import ToolInvocationRequest
from agent.watchdog import Watchdog


@dataclass
class StepLoopResult:
    next_index: int
    result: Optional[ToolResult] = None
    answer: Optional[str] = None
    stop: bool = False


class PlanExecutor:
    """Coordinates a plan while delegating individual steps to StepExecutor."""

    def __init__(self, orchestrator: Any, step_executor: Optional[StepExecutor] = None):
        self.orchestrator = orchestrator
        self.step_executor = step_executor or StepExecutor(orchestrator)
        self._step_dependencies: Dict[int, List[int]] = {}
        self._dependency_files: Dict[tuple[int, int], str] = {}

    def execute(self, objective: str, tool_usage_count: Dict[str, int]) -> Optional[str]:
        state = self.orchestrator.agent_state
        last_result: Optional[ToolResult] = None
        self.orchestrator.workspace.create_restore_point(state.plan)
        self._rebuild_dependency_map()
        index = state.next_pending_index()
        while index is not None:
            iteration = self._execute_index(index, objective, tool_usage_count)
            last_result = iteration.result or last_result
            if iteration.stop:
                return iteration.answer
            index = state.next_pending_index(iteration.next_index)
        if last_result is not None and not last_result.get("ok"):
            return f"A tarefa não pôde ser concluída. Último erro: {last_result.get('error', 'Erro desconhecido')}"
        return None

    def _execute_index(self, index: int, objective: str, usage: Dict[str, int]) -> StepLoopResult:
        state = self.orchestrator.agent_state
        if self.orchestrator.cancellation_token.cancelled:
            return StepLoopResult(index, answer="Tarefa cancelada. O progresso concluído foi preservado.", stop=True)
        step = state.plan[index]
        state.plan_step = index + 1
        blocked = self._check_watchdog() or self._check_cost_limits(index + 1)
        if blocked:
            return StepLoopResult(index, answer=blocked, stop=True)
        if not self._check_dependencies_ok(index):
            self.step_executor.finish_skipped(index, "dependência não satisfeita")
            return StepLoopResult(index + 1)
        tool = str(step.get("tool", ""))
        batch = self._collect_parallel_read_batch(index) if tool in ("file_reader", "directory_lister") else []
        if len(batch) > 1:
            return self._execute_parallel_read_batch(batch, objective, usage)
        outcome = self.step_executor.execute(index, objective, usage)
        if outcome.kind in (
            StepOutcomeKind.FINAL,
            StepOutcomeKind.CANCELLED,
            StepOutcomeKind.BLOCKED,
            StepOutcomeKind.UNVERIFIED,
        ):
            return StepLoopResult(index, outcome.result, outcome.final_answer, True)
        if outcome.kind is StepOutcomeKind.REPLAN:
            return self._handle_replan(index, step, tool, objective, outcome.error, outcome.result)
        return StepLoopResult(index + 1, outcome.result)

    def _handle_replan(
        self, index: int, step: Dict[str, Any], tool: str, objective: str,
        error: str, result: Optional[ToolResult],
    ) -> StepLoopResult:
        raw_args = step.get("args")
        args = cast(ToolArgs, raw_args) if isinstance(raw_args, dict) else {}
        replacements = self._attempt_replan(step, tool, args, objective)
        if replacements:
            self._replace_current_step(index, replacements)
            return StepLoopResult(index, result)
        self.step_executor.finish_failed(index, error)
        return StepLoopResult(index, result, f"A tarefa não pôde ser concluída. Último erro: {error}", True)

    def _collect_parallel_read_batch(self, start_index: int) -> List[int]:
        batch: List[int] = []
        state = self.orchestrator.agent_state
        for index in range(start_index, len(state.plan)):
            if state.get_step_status(index).value != "pending":
                break
            if state.plan[index].get("tool") not in ("file_reader", "directory_lister"):
                break
            if not self._check_dependencies_ok(index):
                break
            batch.append(index)
        return batch

    def _execute_parallel_read_batch(
        self, batch_indices: List[int], objective: str, usage: Dict[str, int]
    ) -> StepLoopResult:
        remaining = self._remaining_tool_call_budget()
        if remaining <= 0:
            answer = self._check_cost_limits(batch_indices[0] + 1)
            return StepLoopResult(batch_indices[0], answer=answer, stop=True)
        dispatch_indices = batch_indices[:remaining]
        cached, results, correlations = self._run_parallel_tools(dispatch_indices)
        return self._finalize_parallel(
            dispatch_indices, cached, results, correlations, objective, usage
        )

    def _run_parallel_tools(
        self, indices: List[int]
    ) -> tuple[Dict[int, ToolResult], Dict[int, ToolResult], Dict[int, ParallelInvocation]]:
        state = self.orchestrator.agent_state
        cached: Dict[int, ToolResult] = {}
        results: Dict[int, ToolResult] = {}
        correlations: Dict[int, ParallelInvocation] = {}
        futures: Dict[concurrent.futures.Future[ToolResult], int] = {}
        workers = min(int(self.orchestrator.session.config.get("max_io_concurrency", 2)), len(indices))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            for index in indices:
                tool, args, file_path = self._step_data(index)
                invocation_id = str(uuid.uuid4())
                correlation = ParallelInvocation(
                    index=index,
                    step_id=state.get_step_id(index),
                    invocation_id=invocation_id,
                    request=ToolInvocationRequest(invocation_id, tool, args),
                )
                correlations[index] = correlation
                state.mark_step_running(index)
                cache_hit, cache_result = self.step_executor.try_cache(
                    tool, args, file_path, correlation.step_id, record_result=False
                )
                if cache_hit and cache_result is not None:
                    cached[index] = correlate_parallel_result(cache_result, correlation)
                else:
                    if not getattr(self.orchestrator, "tool_invocation_gateway", None):
                        self.orchestrator._emit("tool_start", {"tool": tool, "args": args})
                    runner = getattr(self.orchestrator.tool_executor, "run_tool_invocation", None)
                    if callable(runner):
                        future = executor.submit(runner, correlation.request, False)
                    else:
                        future = executor.submit(
                            self.orchestrator.tool_executor.run_tool, tool, args, False
                        )
                    futures[future] = index
            for future in concurrent.futures.as_completed(futures):
                index = futures[future]
                results[index] = future_parallel_result(future, correlations[index])
        return cached, results, correlations

    def _finalize_parallel(
        self, indices: List[int], cached: Dict[int, ToolResult], results: Dict[int, ToolResult],
        correlations: Dict[int, ParallelInvocation],
        objective: str, usage: Dict[str, int],
    ) -> StepLoopResult:
        last_result: Optional[ToolResult] = None
        terminal: tuple[int, StepLoopResult] | None = None
        replan_request: tuple[int, Dict[str, Any], str, ToolArgs, ToolResult, str] | None = None
        for index in indices:
            outcome, result = finalize_parallel_index(
                self, index, cached, results, correlations, objective, usage
            )
            last_result = result
            if outcome.kind in (
                StepOutcomeKind.FINAL,
                StepOutcomeKind.CANCELLED,
                StepOutcomeKind.BLOCKED,
                StepOutcomeKind.UNVERIFIED,
            ) and terminal is None:
                terminal = (
                    index,
                    StepLoopResult(index + 1, result, outcome.final_answer, True),
                )
            if outcome.kind is StepOutcomeKind.REPLAN:
                step = self.orchestrator.agent_state.plan[index]
                tool, args, _ = self._step_data(index)
                replan_request = (index, step, tool, args, result, outcome.error)
        if terminal is not None:
            terminal_index, terminal_result = terminal
            terminal_tool, terminal_args, _ = self._step_data(terminal_index)
            self.orchestrator.agent_state.project_last_result(
                terminal_tool, terminal_args, terminal_result.result or {}
            )
            return terminal_result
        if replan_request is not None:
            index, step, tool, args, result, error = replan_request
            replacements = self._attempt_replan(
                step, tool, args, objective, last_result=result, last_error=error
            )
            if replacements:
                self._replace_current_step(index, replacements)
                return StepLoopResult(index, result)
            self.step_executor.finish_failed(index, error, result)
            return StepLoopResult(
                index, result, f"A tarefa não pôde ser concluída. Último erro: {error}", True
            )
        return StepLoopResult(indices[-1] + 1, last_result)

    def _step_data(self, index: int) -> tuple[str, ToolArgs, str]:
        step = self.orchestrator.agent_state.plan[index]
        raw_args = step.get("args")
        args = cast(ToolArgs, raw_args) if isinstance(raw_args, dict) else {}
        return str(step.get("tool", "")), args, str(args.get("target") or args.get("file_path") or "")

    def _build_dependency_map(self, plan: List[Dict[str, Any]]) -> Dict[int, List[int]]:
        dependencies: Dict[int, List[int]] = {}
        self._dependency_files = {}
        producers: Dict[str, int] = {}
        for index, step in enumerate(plan):
            raw_args = step.get("args")
            args = cast(ToolArgs, raw_args) if isinstance(raw_args, dict) else {}
            file_path = str(args.get("file_path") or args.get("target") or "")
            if step.get("tool") == "file_writer" and file_path:
                producers[file_path] = index
            elif step.get("tool") in ("file_reader", "code_analyzer") and file_path in producers:
                producer = producers[file_path]
                dependencies.setdefault(index, []).append(producer)
                self._dependency_files[(index, producer)] = file_path
        return dependencies

    def _check_dependencies_ok(self, index: int) -> bool:
        for producer in self._step_dependencies.get(index, []):
            if not self._dependency_succeeded(index, producer):
                step = self.orchestrator.agent_state.plan[index]
                result = {"ok": False, "error": f"Dependência falhou: passo {producer + 1}"}
                self.orchestrator.agent_state.record_tool_result(str(step.get("tool", "unknown")), step.get("args", {}), result)
                return False
        return True

    def _dependency_succeeded(self, index: int, producer: int) -> bool:
        file_path = self._dependency_files.get((index, producer))
        matching = [item for item in self.orchestrator.agent_state.tool_history if item.get("tool") == "file_writer" and (item.get("args") or {}).get("file_path") == file_path]
        return bool(matching and matching[-1].get("result", {}).get("ok"))

    def _attempt_replan(
        self, step: Dict[str, Any], tool: str, args: ToolArgs, objective: str,
        *, last_result: Optional[ToolResult] = None, last_error: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        del tool, args
        state = self.orchestrator.agent_state
        selected_result = last_result if last_result is not None else state.last_result
        error = str(last_error if last_error is not None else (selected_result.get("error", "") if selected_result else ""))
        context = ReplanContext(
            task=objective,
            current_step=step,
            tool_history=state.tool_history,
            last_exception=error,
            last_tool_result=cast(Dict[str, Any], selected_result) if selected_result else None,
        )
        action = replan(context, error, self.orchestrator)
        return action.steps if action else None

    def _replace_current_step(self, index: int, new_steps: List[Dict[str, Any]]) -> None:
        self.orchestrator.agent_state.replace_plan_step(index, new_steps)
        self._rebuild_dependency_map()

    def _rebuild_dependency_map(self) -> None:
        self._step_dependencies = self._build_dependency_map(self.orchestrator.agent_state.plan)

    def _check_cost_limits(self, step_number: int) -> Optional[str]:
        state, config = self.orchestrator.agent_state, self.orchestrator.session.config
        tokens = self.orchestrator.context_manager.estimate_conversation_tokens()
        if not CostGuard.check_limits(step_number, state.tool_history, tokens, config):
            return None
        self.orchestrator._emit("cost_limit", CostGuard.build_limit_reached_event(step_number, state.tool_history, tokens, config))
        answer = str(CostGuard.build_limit_summary(state.objective, state.tool_history, state.last_result))
        state.add_conversation_turn(str(state.objective), answer)
        self.orchestrator.fail_task()
        return answer

    def _remaining_tool_call_budget(self) -> int:
        configured = self.orchestrator.session.config.get(
            "max_task_tool_calls", DEFAULT_MAX_TASK_TOOL_CALLS,
        )
        return max(0, int(configured) - len(self.orchestrator.agent_state.tool_history))

    def _check_watchdog(self) -> Optional[str]:
        state = self.orchestrator.agent_state
        reason = Watchdog.check_all(self.orchestrator._task_start_time, state.tool_history, self.orchestrator.session.config)
        if not reason:
            return None
        self.orchestrator._emit("watchdog", Watchdog.build_watchdog_event(reason, self.orchestrator._task_start_time))
        answer = str(Watchdog.build_watchdog_summary(state.tool_history, reason))
        state.add_conversation_turn(str(state.objective), answer)
        self.orchestrator.fail_task()
        return answer
