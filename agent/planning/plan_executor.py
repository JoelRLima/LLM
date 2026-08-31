from typing import Any, Dict, List, Optional

from agent.planning.deferred_condition import is_deferred_condition
from agent.planning.deferred_execution import execute_deferred_condition
from agent.planning.execution_models import StepLoopResult
from agent.planning.parallel_contracts import ParallelInvocation
from agent.planning.parallel_dispatch import run_parallel_tools
from agent.planning.parallel_finalizer import finalize_parallel_index
from agent.planning.plan_execution_loop import run_plan_loop
from agent.planning.plan_executor_support import PlanExecutorSupportMixin
from agent.planning.plan_model import ToolPlanStep
from agent.planning.semantic_projection import (
    SemanticProjection,
    project_outcomes,
    projection_for_outcome,
)
from agent.planning.step_executor import StepExecutor, StepOutcomeKind
from agent.planning.task_policy_support import policy_terminal_answer
from agent.runtime.failures import FailureFact
from agent.tools.contracts import ToolResult


class PlanExecutor(PlanExecutorSupportMixin):
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
        policy = getattr(self.orchestrator, "task_policy", None)
        if policy is not None:
            step = state.plan[index]
            if not self._check_dependencies_ok(index):
                self.step_executor.finish_skipped(index, "dependency not satisfied")
                state.set_plan_step(index + 1)
                return StepLoopResult(index + 1)
            if isinstance(step, ToolPlanStep) and step.tool in ("file_reader", "directory_lister"):
                candidate_batch = self._collect_parallel_read_batch(index)
                if len(candidate_batch) > 1:
                    return self._execute_parallel_read_batch(candidate_batch, objective, usage)
            admission = policy.admit_work_units(
                1,
                resource="tool_calls" if isinstance(step, ToolPlanStep) else None,
                watchdog_reason=self._watchdog_reason(),
            )
            blocked = policy_terminal_answer(self.orchestrator, admission, step_index=index)
            if blocked is not None:
                return StepLoopResult(index, answer=blocked, stop=True)
        if policy is None and self.orchestrator.cancellation_token.cancelled:
            return StepLoopResult(index, answer="Tarefa cancelada. O progresso concluído foi preservado.", stop=True)
        state.set_plan_step(index + 1)
        blocked = (
            None
            if policy is not None
            else self._check_watchdog() or self._check_cost_limits(index + 1)
        )
        if blocked:
            return StepLoopResult(index, answer=blocked, stop=True)
        return self._execute_admitted_step(index, objective, usage)

    def _execute_admitted_step(
        self, index: int, objective: str, usage: Dict[str, int]
    ) -> StepLoopResult:
        state = self.orchestrator.agent_state
        step = state.plan[index]
        if is_deferred_condition(step):
            return self._execute_deferred_condition(index, objective)
        if not self._check_dependencies_ok(index):
            self.step_executor.finish_skipped(index, "dependência não satisfeita")
            return StepLoopResult(index + 1)
        if not isinstance(step, ToolPlanStep):
            return StepLoopResult(index, answer="Passo executável não é um ToolPlanStep.", stop=True)
        tool = step.tool
        batch = self._collect_parallel_read_batch(index) if tool in ("file_reader", "directory_lister") else []
        if len(batch) > 1:
            return self._execute_parallel_read_batch(batch, objective, usage)
        outcome = self.step_executor.execute(index, objective, usage)
        self.last_projection = projection_for_outcome(index, outcome)
        return self._resolve_step_outcome(index, objective, step, tool, outcome)

    def _resolve_step_outcome(
        self,
        index: int,
        objective: str,
        step: ToolPlanStep,
        tool: str,
        outcome: Any,
    ) -> StepLoopResult:
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
        self, index: int, step: ToolPlanStep, tool: str, objective: str,
        error: str, result: Optional[ToolResult], failure: FailureFact | None,
    ) -> StepLoopResult:
        args = dict(step.args)
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
            step = state.plan[index]
            if not isinstance(step, ToolPlanStep) or step.tool not in (
                "file_reader", "directory_lister"
            ):
                break
            if not self._check_dependencies_ok(index):
                break
            batch.append(index)
        return batch
    def _execute_parallel_read_batch(
        self, batch_indices: List[int], objective: str, usage: Dict[str, int]
    ) -> StepLoopResult:
        policy = getattr(self.orchestrator, "task_policy", None)
        if policy is not None:
            admission = policy.admit_work_units(
                len(batch_indices),
                resource="tool_calls",
                watchdog_reason=self._watchdog_reason(),
            )
            blocked = policy_terminal_answer(
                self.orchestrator,
                admission,
                step_index=batch_indices[0],
            )
            if blocked is not None:
                return StepLoopResult(batch_indices[0], answer=blocked, stop=True)
            dispatch_indices = batch_indices[: admission.admitted_units]
            if not dispatch_indices:
                return StepLoopResult(
                    batch_indices[0], answer="Nenhum passo foi admitido.", stop=True
                )
            cached, results, correlations = self._run_parallel_tools(dispatch_indices)
            return self._finalize_parallel(
                dispatch_indices, cached, results, correlations, objective, usage
            )
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
