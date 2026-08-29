"""Dependency, recovery and guard helpers for :mod:`plan_executor`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Dict, List, Optional

from agent.contracts import ToolArgs
from agent.cost_guard import CostGuard
from agent.planning.dependency_map import (
    build_dependency_map,
    dependency_succeeded,
    dependent_indices,
)
from agent.planning.plan_model import Plan, ToolPlanStep
from agent.planning.replan_execution import attempt_replan
from agent.runtime.budget import task_budget_for
from agent.runtime.failures import FailureFact
from agent.tools.contracts import ToolError, ToolResult, ToolStatus
from agent.watchdog import Watchdog


class PlanExecutorSupportMixin:
    orchestrator: Any
    _step_dependencies: Dict[int, List[int]]
    _dependency_files: Dict[tuple[int, int], str]

    def _step_data(self, index: int) -> tuple[str, ToolArgs, str]:
        step = self.orchestrator.agent_state.plan[index]
        if not isinstance(step, ToolPlanStep):
            return "", {}, ""
        args = dict(step.args)
        return step.tool, args, str(args.get("target") or args.get("file_path") or "")

    def _build_dependency_map(
        self, plan: Plan | Sequence[Mapping[str, Any]]
    ) -> Dict[int, List[int]]:
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
                tool = step.tool if isinstance(step, ToolPlanStep) else "deferred_condition"
                args = dict(step.args) if isinstance(step, ToolPlanStep) else {}
                self.orchestrator.agent_state.record_tool_result(tool, args, result)
                return False
        return True

    def _dependency_succeeded(self, index: int, producer: int) -> bool:
        return dependency_succeeded(
            self.orchestrator.agent_state.tool_history,
            self.orchestrator.agent_state.get_step_id(producer),
            plan_id=getattr(self.orchestrator.agent_state, "plan_identity", None),
        )

    def _attempt_replan(
        self,
        step: ToolPlanStep,
        tool: str,
        args: ToolArgs,
        objective: str,
        *,
        last_result: Optional[ToolResult] = None,
        last_error: Optional[str] = None,
        failure: FailureFact | None = None,
    ) -> Optional[Plan]:
        del tool, args
        return attempt_replan(
            self.orchestrator,
            step,
            objective,
            last_result=last_result,
            last_error=last_error,
            failure=failure,
        )

    def _replace_current_step(
        self,
        index: int,
        new_steps: Plan | Sequence[Mapping[str, Any]],
    ) -> bool:
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
        answer = str(
            CostGuard.build_limit_summary(
                state.objective, state.tool_history, state.last_result
            )
        )
        state.add_conversation_turn(str(state.objective), answer)
        self.orchestrator.fail_task()
        return answer

    def _remaining_tool_call_budget(self) -> int:
        ledger = task_budget_for(self.orchestrator, self.orchestrator.session.config)
        return ledger.remaining_tool_calls

    def _check_watchdog(self) -> Optional[str]:
        state = self.orchestrator.agent_state
        reason = Watchdog.check_all(
            self.orchestrator._task_start_time,
            state.tool_history,
            self.orchestrator.session.config,
        )
        if not reason:
            return None
        self.orchestrator._emit(
            "watchdog",
            Watchdog.build_watchdog_event(reason, self.orchestrator._task_start_time),
        )
        answer = str(Watchdog.build_watchdog_summary(state.tool_history, reason))
        state.add_conversation_turn(str(state.objective), answer)
        self.orchestrator.fail_task()
        return answer
