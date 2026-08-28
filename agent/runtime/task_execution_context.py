"""Construction of the one application-owned nested execution context."""

from __future__ import annotations

from typing import Any, cast

from agent.capabilities import ALL_CAPABILITIES
from agent.runtime.budget import TaskBudgetLedger
from agent.runtime.context import RuntimeLimits, TaskExecutionContext


def build_task_execution_context(owner: Any) -> TaskExecutionContext:
    config = owner.session.config or {}
    return TaskExecutionContext(
        model_gateway=owner.session.gateway,
        model_profile=getattr(owner.session, "model_profile", None),
        cancellation=owner.cancellation_token,
        limits=RuntimeLimits.from_config(config),
        budget_ledger=owner.task_budget,
        permissions=frozenset(item.value for item in ALL_CAPABILITIES),
        metadata={"ownership_root": True, "workspace_manager": owner.workspace},
    )


class TaskExecutionOwnershipMixin:
    _planning_context: Any
    _task_execution_context: TaskExecutionContext | None

    @property
    def task_execution_context(self: Any) -> TaskExecutionContext:
        """Canonical root for application-owned nested execution."""

        if self._task_execution_context is None:
            self._task_execution_context = build_task_execution_context(self)
        return cast(TaskExecutionContext, self._task_execution_context)

    def _reset_task_execution_context(self: Any) -> None:
        if self._task_execution_context is not None:
            self._task_execution_context = self._task_execution_context.new_task()

    def _reset_task_state(self: Any, objective: str) -> None:
        assert isinstance(self.task_budget, TaskBudgetLedger)
        self.task_budget.reset()
        self.agent_state.objective = objective
        self.agent_state.reset_execution()
        self.agent_state.reset_task_progression()
        self.agent_state.reset_runtime_observation(clear_events=True)
        self.context_manager._cached_project_context = None
        self.workspace.restore_points.clear()
        created_files = getattr(self.workspace, "created_files", None)
        clear_created_files = getattr(created_files, "clear", None)
        if callable(clear_created_files):
            clear_created_files()
        discard_transactions = getattr(self.workspace, "discard_transactions", None)
        if callable(discard_transactions):
            discard_transactions()
        self._planning_context = None
        self._task_failed = False
        self._cancelled = False
        self.cancellation_token.reset()
        reset_context = getattr(self, "_reset_task_execution_context", None)
        if callable(reset_context):
            reset_context()


__all__ = ["TaskExecutionOwnershipMixin", "build_task_execution_context"]
