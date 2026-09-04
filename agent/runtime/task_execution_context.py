"""Construction of the one application-owned nested execution context."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from agent.capabilities import ALL_CAPABILITIES
from agent.runtime.budget import TaskBudgetLedger
from agent.runtime.context import RuntimeLimits, TaskExecutionContext
from agent.runtime.correlation import RunCorrelation


def ensure_runtime_correlation(owner: Any) -> RunCorrelation:
    """Ensure exceptional pre-run identity at the runtime ownership boundary."""

    current = getattr(owner, "_run_correlation", None)
    if isinstance(current, RunCorrelation):
        return current
    ensure = getattr(owner, "_ensure_run_correlation", None)
    if callable(ensure):
        selected = ensure()
        if isinstance(selected, RunCorrelation):
            return selected
    state = getattr(owner, "agent_state", None)
    correlation = RunCorrelation.fresh()
    owner._run_correlation = correlation
    owner._run_id = correlation.run_id
    if state is not None:
        state.root_task_id = correlation.root_task_id
        state.runtime_correlation = correlation
    session = getattr(owner, "session", None)
    if session is not None:
        session.run_correlation = correlation
    return correlation


def build_task_execution_context(owner: Any) -> TaskExecutionContext:
    config = owner.session.config or {}
    correlation = owner._ensure_run_correlation()
    return TaskExecutionContext(
        model_gateway=owner.session.gateway,
        model_profile=getattr(owner.session, "model_profile", None),
        cancellation=owner.cancellation_token,
        limits=RuntimeLimits.from_config(config),
        budget_ledger=owner.task_budget,
        policy_state=getattr(owner.agent_state, "task_policy_state", None),
        recovery_budget=getattr(owner.agent_state, "recovery_budget", None),
        task_policy=getattr(owner, "task_policy", None),
        correlation=correlation,
        event_sink=getattr(owner, "event_dispatcher", None),
        permissions=frozenset(item.value for item in ALL_CAPABILITIES),
        metadata={"ownership_root": True, "workspace_manager": owner.workspace},
    )


class TaskExecutionOwnershipMixin:
    _planning_context: Any
    _task_execution_context: TaskExecutionContext | None
    _run_correlation: RunCorrelation | None
    _run_id: str | None

    @property
    def run_correlation(self: Any) -> RunCorrelation:
        """Return the application-owned correlation for the active attempt."""

        return cast(RunCorrelation, self._ensure_run_correlation())

    @property
    def task_execution_context(self: Any) -> TaskExecutionContext:
        """Canonical root for application-owned nested execution."""

        if self._task_execution_context is None:
            self._task_execution_context = build_task_execution_context(self)
        return cast(TaskExecutionContext, self._task_execution_context)

    def _reset_task_execution_context(self: Any) -> None:
        # A fresh root is established by the normal preparation boundary.
        # Dropping the old context here prevents a previous task from leaking
        # its correlation into the next attempt.
        self._task_execution_context = None

    def _start_run_correlation(self: Any, *, resumed: bool = False) -> RunCorrelation:
        root_task_id = getattr(self.agent_state, "root_task_id", None) if resumed else None
        correlation = RunCorrelation.resume(root_task_id) if resumed else RunCorrelation.fresh()
        self._run_correlation = correlation
        self._run_id = correlation.run_id
        self.agent_state.root_task_id = correlation.root_task_id
        self.agent_state.runtime_correlation = correlation
        self.session.run_correlation = correlation
        policy = getattr(self, "task_policy", None)
        if policy is not None:
            policy.set_correlation(correlation)
        event_dispatcher = getattr(self, "event_dispatcher", None)
        if event_dispatcher is not None:
            self.session.event_sink = event_dispatcher
        if self._task_execution_context is not None:
            self._task_execution_context = replace(
                self._task_execution_context,
                correlation=correlation,
                task_id=correlation.task_id,
                parent_task_id=None,
                node_id=None,
            )
        return correlation

    def _ensure_run_correlation(self: Any) -> RunCorrelation:
        correlation = getattr(self, "_run_correlation", None)
        if correlation is None:
            # This is reserved for exceptional pre-run paths that request a
            # context/result before TaskRunner preparation.  The same owner
            # still creates the IDs.
            correlation = cast(
                RunCorrelation,
                self._start_run_correlation(resumed=False),
            )
        return cast(RunCorrelation, correlation)

    def _reset_task_state(self: Any, objective: str) -> None:
        assert isinstance(self.task_budget, TaskBudgetLedger)
        self.task_budget.reset()
        self.agent_state.objective = objective
        self.agent_state.task_run_directive = None
        self.agent_state.task_definition_ref = None
        self.agent_state.reset_execution()
        self.agent_state.reset_task_progression()
        self.agent_state.reset_runtime_observation(clear_events=True)
        self.agent_state.hierarchical_lifecycle = {"status": "inactive"}
        policy_state = getattr(self.agent_state, "task_policy_state", None)
        reset_policy = getattr(policy_state, "reset", None)
        if callable(reset_policy):
            reset_policy()
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


__all__ = [
    "TaskExecutionOwnershipMixin",
    "build_task_execution_context",
    "ensure_runtime_correlation",
]
