from typing import Any, Dict, List, Optional, cast

from agent.contracts import ToolArgs
from agent.cost_guard import CostGuard
from agent.planning.deferred_condition import is_deferred_condition
from agent.planning.deferred_execution import execute_deferred_condition
from agent.planning.dependency_map import build_dependency_map, dependency_succeeded, dependent_indices
from agent.planning.execution_models import StepLoopResult
from agent.planning.parallel_contracts import ParallelInvocation
from agent.planning.parallel_dispatch import run_parallel_tools
from agent.planning.parallel_finalizer import finalize_parallel_index
from agent.planning.plan_execution_loop import run_plan_loop
from agent.planning.replan_execution import attempt_replan
from agent.planning.semantic_projection import (
    SemanticProjection,
    project_outcomes,
    projection_for_outcome,
)
from agent.planning.step_executor import StepExecutor, StepOutcomeKind
from agent.runtime.budget import task_budget_for
from agent.runtime.failures import FailureFact
from agent.tools.contracts import ToolError, ToolResult, ToolStatus
from agent.watchdog import Watchdog


class PlanExecutor:
    """Coordinates a plan while delegating individual steps to StepExecutor."""

    def __init__(self, orchestrator: Any, step_executor: Optional[StepExecutor] = None):
        self.orchestrator = orchestrator
        self.step_executor = step_executor or StepExecutor(orchestrator)
        self._step_dependencies: Dict[int, List[int]] = {}
        self._dependency_files: Dict[tuple[int, int], str] = {}
        self.last_projection: Optional[SemanticProjection] = None

    def execute(
        self,
        objective: str,
        tool_usage_count: Dict[str, int],
        *,
        continue_after_plan: bool = False,
    ) -> Optional[str]:
        state = self.orchestrator.agent_state
        self.last_projection = None
        self.orchestrator.workspace.create_restore_point(state.plan)
        self._rebuild_dependency_map()
        return run_plan_loop(self, objective, tool_usage_count, continue_after_plan)
    def _execute_index(self, index: int, objective: str, usage: Dict[str, int]) -> StepLoopResult:
        state = self.orchestrator.agent_state
        if self.orchestrator.cancellation_token.cancelled:
            return StepLoopResult(index, answer="Tarefa cancelada. O progresso concluído foi preservado.", stop=True)
        step = state.plan[index]
        state.set_plan_step(index + 1)
        blocked = self._check_watchdog() or self._check_cost_limits(index + 1)
        if blocked:
            return StepLoopResult(index, answer=blocked, stop=True)
        if is_deferred_condition(step):
            return self._execute_deferred_condition(index, objective)
        if not self._check_dependencies_ok(index):
            self.step_executor.finish_skipped(index, "dependência não satisfeita")
            return StepLoopResult(index + 1)
        tool = str(step.get("tool", ""))
        batch = self._collect_parallel_read_batch(index) if tool in ("file_reader", "directory_lister") else []
        if len(batch) > 1:
            return self._execute_parallel_read_batch(batch, objective, usage)
        outcome = self.step_executor.execute(index, objective, usage)
        self.last_projection = projection_for_outcome(index, outcome)
        if outcome.kind in (
            StepOutcomeKind.FINAL,
            StepOutcomeKind.CANCELLED,
            StepOutcomeKind.BLOCKED,
            StepOutcomeKind.UNVERIFIED,
            StepOutcomeKind.PERMISSION_DENIED,
        ):
            return StepLoopResult(index, outcome.result, outcome.final_answer, True)
        if outcome.kind is StepOutcomeKind.REPLAN:
            return self._handle_replan(
                index,
                step,
                tool,
                objective,
                outcome.error,
                outcome.result,
                outcome.failure,
            )
        return StepLoopResult(index + 1, outcome.result)

    def _execute_deferred_condition(self, index: int, objective: str) -> StepLoopResult:
        return execute_deferred_condition(self, index, objective)
    def _handle_replan(
        self, index: int, step: Dict[str, Any], tool: str, objective: str,
        error: str, result: Optional[ToolResult], failure: FailureFact | None,
    ) -> StepLoopResult:
        raw_args = step.get("args")
        args = cast(ToolArgs, raw_args) if isinstance(raw_args, dict) else {}
        replacements = self._attempt_replan(
            step, tool, args, objective, failure=failure
        )
        if replacements:
            if self._replace_current_step(index, replacements):
                return StepLoopResult(index, result)
            return StepLoopResult(index, result, "Replanejamento bloqueado: havia dependencias ja executadas.", True)
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
        return run_parallel_tools(self, indices)
    def _finalize_parallel(
        self, indices: List[int], cached: Dict[int, ToolResult], results: Dict[int, ToolResult],
        correlations: Dict[int, ParallelInvocation],
        objective: str, usage: Dict[str, int],
    ) -> StepLoopResult:
        finalized: list[tuple[int, Any, ToolResult]] = []
        for index in indices:
            outcome, result = finalize_parallel_index(
                self, index, cached, results, correlations, objective, usage
            )
            finalized.append((index, outcome, result))
        projection = project_outcomes(finalized)
        if projection is None:
            return StepLoopResult(indices[-1] + 1)
        self.last_projection = projection
        projection_correlation = correlations[projection.logical_slot]
        projection_tool = projection_correlation.request.tool_name
        projection_args = dict(projection_correlation.request.arguments)
        self.orchestrator.agent_state.project_last_result(
            projection_tool, projection_args, projection.result
        )
        if projection.outcome.kind is StepOutcomeKind.REPLAN:
            index = projection.logical_slot
            step = self.orchestrator.agent_state.plan[index]
            correlation = correlations[index]
            tool = correlation.request.tool_name
            args = dict(correlation.request.arguments)
            result, error = projection.result, projection.outcome.error
            replacements = self._attempt_replan(
                step,
                tool,
                args,
                objective,
                last_result=result,
                last_error=error,
                failure=projection.outcome.failure,
            )
            if replacements:
                if self._replace_current_step(index, replacements):
                    return StepLoopResult(index, result)
                return StepLoopResult(index, result, "Replanejamento bloqueado: havia dependencias ja executadas.", True)
            return StepLoopResult(index, result, f"A tarefa não pôde ser concluída. Último erro: {error}", True)
            return StepLoopResult(
                index, result, f"A tarefa não pôde ser concluída. Último erro: {error}", True
            )
        return StepLoopResult(
            projection.logical_slot + 1,
            projection.result,
            projection.outcome.final_answer,
            projection.decisive,
        )

    def _step_data(self, index: int) -> tuple[str, ToolArgs, str]:
        step = self.orchestrator.agent_state.plan[index]
        raw_args = step.get("args")
        args = cast(ToolArgs, raw_args) if isinstance(raw_args, dict) else {}
        return str(step.get("tool", "")), args, str(args.get("target") or args.get("file_path") or "")

    def _build_dependency_map(self, plan: List[Dict[str, Any]]) -> Dict[int, List[int]]:
        dependencies, self._dependency_files = build_dependency_map(plan)
        return dependencies

    def _check_dependencies_ok(self, index: int) -> bool:
        for producer in self._step_dependencies.get(index, []):
            if not self._dependency_succeeded(index, producer):
                step = self.orchestrator.agent_state.plan[index]
                result = ToolResult(
                    invocation_id=f"dependency:{index + 1}",
                    status=ToolStatus.FAILED,
                    error=ToolError(
                        "DEPENDENCY_FAILED",
                        f"Dependência falhou: passo {producer + 1}",
                    ),
                    executed=False,
                )
                self.orchestrator.agent_state.record_tool_result(str(step.get("tool", "unknown")), step.get("args", {}), result)
                return False
        return True

    def _dependency_succeeded(self, index: int, producer: int) -> bool:
        return dependency_succeeded(
            self.orchestrator.agent_state.tool_history,
            self.orchestrator.agent_state.get_step_id(producer),
            plan_id=getattr(self.orchestrator.agent_state, "plan_identity", None),
        )

    def _attempt_replan(
        self, step: Dict[str, Any], tool: str, args: ToolArgs, objective: str,
        *,
        last_result: Optional[ToolResult] = None,
        last_error: Optional[str] = None,
        failure: FailureFact | None = None,
    ) -> Optional[List[Dict[str, Any]]]:
        del tool, args
        return attempt_replan(
            self.orchestrator,
            step,
            objective,
            last_result=last_result,
            last_error=last_error,
            failure=failure,
        )

    def _replace_current_step(self, index: int, new_steps: List[Dict[str, Any]]) -> bool:
        """Replace a failed producer without silently rewinding consumers."""

        state = self.orchestrator.agent_state
        producer_id = state.get_step_id(index)
        dependents = dependent_indices(state.plan, index)
        for dependent in sorted(dependents):
            if dependent >= len(state.plan):
                continue
            status = state.get_step_status(dependent)
            if status.value in {"running", "completed"}:
                self.orchestrator._emit(
                    "replan_blocked",
                    {
                        "step_id": producer_id,
                        "reason": "dependencia causal ja executada",
                    },
                )
                self.orchestrator.fail_task()
                return False
        for dependent in sorted(dependents, reverse=True):
            if dependent < len(state.plan):
                state.remove_plan_step(dependent)
        state.replace_plan_step(index, new_steps)
        self._rebuild_dependency_map()
        return True

    def _rebuild_dependency_map(self) -> None:
        self._step_dependencies = self._build_dependency_map(self.orchestrator.agent_state.plan)

    def _check_cost_limits(self, step_number: int) -> Optional[str]:
        state, config = self.orchestrator.agent_state, self.orchestrator.session.config
        ledger = task_budget_for(self.orchestrator, config)
        if not CostGuard.check_limits(step_number, state.tool_history, 0, config, ledger):
            return None
        self.orchestrator._emit(
            "cost_limit",
            CostGuard.build_limit_reached_event(
                step_number, state.tool_history, 0, config, ledger
            ),
        )
        answer = str(CostGuard.build_limit_summary(state.objective, state.tool_history, state.last_result))
        state.add_conversation_turn(str(state.objective), answer)
        self.orchestrator.fail_task()
        return answer

    def _remaining_tool_call_budget(self) -> int:
        ledger = task_budget_for(self.orchestrator, self.orchestrator.session.config)
        return ledger.remaining_tool_calls

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
