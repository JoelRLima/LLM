"""Runtime-side adapters for binding the canonical task policy."""

from __future__ import annotations

from typing import Any

from agent.capabilities import ALL_CAPABILITIES
from agent.runtime.context import RuntimeLimits, TaskExecutionContext
from agent.runtime.task_policy import TaskRuntimePolicy


def refresh_orchestrator_task_policy(orchestrator: Any) -> None:
    """Bind one policy to the orchestrator's existing task-owned state."""

    policy = TaskRuntimePolicy(
        RuntimeLimits.from_config(orchestrator.session.config),
        state=orchestrator.agent_state.task_policy_state,
        budget_ledger=orchestrator.task_budget,
        recovery_budget=orchestrator.agent_state.recovery_budget,
        cancellation=orchestrator.cancellation_token,
        event_sink=getattr(orchestrator, "event_dispatcher", None),
        correlation=getattr(orchestrator, "_run_correlation", None),
    )
    orchestrator.task_policy = policy
    orchestrator.session.task_policy = policy
    if orchestrator._task_execution_context is not None:
        orchestrator._task_execution_context = TaskExecutionContext(
            model_gateway=orchestrator.session.gateway,
            model_profile=getattr(orchestrator.session, "model_profile", None),
            cancellation=orchestrator.cancellation_token,
            limits=RuntimeLimits.from_config(orchestrator.session.config),
            budget_ledger=orchestrator.task_budget,
            policy_state=orchestrator.agent_state.task_policy_state,
            recovery_budget=orchestrator.agent_state.recovery_budget,
            task_policy=policy,
            correlation=orchestrator.run_correlation,
            event_sink=getattr(orchestrator, "event_dispatcher", None),
            permissions=frozenset(item.value for item in ALL_CAPABILITIES),
            metadata={"ownership_root": True, "workspace_manager": orchestrator.workspace},
        )


__all__ = ["refresh_orchestrator_task_policy"]
