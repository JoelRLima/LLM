"""Construction of the one application-owned nested execution context."""

from __future__ import annotations

from typing import Any, cast

from agent.runtime.budget import TaskBudgetLedger
from agent.runtime.context import RuntimeLimits, TaskExecutionContext


def build_task_execution_context(owner: Any) -> TaskExecutionContext:
    config = owner.session.config or {}
    return TaskExecutionContext(
        model_gateway=owner.session.gateway,
        cancellation=owner.cancellation_token,
        limits=RuntimeLimits(
            max_model_concurrency=max(1, int(config.get("max_model_concurrency", 1))),
            max_io_concurrency=max(1, int(config.get("max_io_concurrency", 2))),
            max_process_concurrency=max(1, int(config.get("max_process_concurrency", 1))),
            max_steps=max(1, int(config.get("max_task_steps", 30))),
            max_model_calls=max(1, int(config.get("max_model_calls", 20))),
            max_task_tool_calls=max(1, int(config.get("max_task_tool_calls", 60))),
            max_task_tokens=max(1, int(config.get("max_task_tokens", 200_000))),
        ),
        budget_ledger=owner.task_budget,
        permissions=frozenset(
            {
                "read", "write", "validate", "analyze", "process", "network",
                "memory", "vcs_read", "vcs_write", "package_install",
            }
        ),
        metadata={"ownership_root": True},
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
        self.agent_state.last_result = None
        self.agent_state.last_tool = None
        self.agent_state.last_args = None
        self.agent_state.tool_history = []
        self.agent_state.events.clear()
        self.context_manager._cached_project_context = None
        self.workspace.restore_points.clear()
        self._planning_context = None
        self._task_failed = False
        self._cancelled = False
        self.cancellation_token.reset()
        reset_context = getattr(self, "_reset_task_execution_context", None)
        if callable(reset_context):
            reset_context()


__all__ = ["TaskExecutionOwnershipMixin", "build_task_execution_context"]
