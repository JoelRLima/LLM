"""Public task-scoped runtime policy seam.

The policy composes existing limits, quantitative ledgers, recovery state, and
cancellation. It owns only logical admissions and active elapsed time.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from agent.runtime.recovery import RecoveryScope
from agent.runtime.task_policy_engine import (
    _publish as publish,
)
from agent.runtime.task_policy_engine import (
    authorize_recovery,
    decide,
)
from agent.runtime.task_policy_state import TaskPolicyState
from agent.runtime.task_policy_types import (
    TaskPolicyDecision,
    TaskPolicyError,
    TaskPolicyResult,
)


class TaskRuntimePolicy:
    """The single narrow task-scoped decision seam."""

    PRECEDENCE = (
        "cancellation",
        "quantitative",
        "logical",
        "active_wall",
        "watchdog",
        "recovery",
    )

    def __init__(
        self,
        limits: Any,
        *,
        state: TaskPolicyState | None = None,
        budget_ledger: Any = None,
        recovery_budget: Any = None,
        cancellation: Any = None,
        clock: Callable[[], float] | None = None,
        event_sink: Any = None,
        correlation: Any = None,
    ) -> None:
        self.limits = limits
        self.state = state or TaskPolicyState()
        self.budget_ledger = budget_ledger
        self.recovery_budget = recovery_budget
        self.cancellation = cancellation
        self._clock = clock or time.monotonic
        self._event_sink = event_sink
        self._correlation = correlation

    @property
    def policy_state(self) -> TaskPolicyState:
        return self.state

    @property
    def logical_work_units_consumed(self) -> int:
        return self.state.logical_work_units_consumed

    @property
    def active_elapsed_seconds(self) -> float:
        return self.state.active_elapsed_at(self._clock())

    def set_correlation(self, correlation: Any) -> None:
        self._correlation = correlation

    def start_active_segment(self) -> None:
        self.state.start_active_segment(self._clock())

    def pause_active_segment(self) -> float:
        return self.state.pause_active_segment(self._clock())

    def reset(self) -> None:
        self.state.reset()

    def remaining_work_units(self) -> int:
        return self.state.remaining_logical_work_units(self._limit("max_steps"))

    def check_current(
        self,
        *,
        resource: str | None = None,
        token_allowance: int = 0,
        watchdog_reason: str | None = None,
        recovery_scope: RecoveryScope | str | None = None,
    ) -> TaskPolicyResult:
        """Check current facts without consuming a logical work unit."""

        return self._decide(
            requested_units=0,
            resource=resource,
            token_allowance=token_allowance,
            watchdog_reason=watchdog_reason,
            recovery_scope=recovery_scope,
            consume=False,
        )

    def admit_work_units(
        self,
        requested_units: int = 1,
        *,
        resource: str | None = None,
        token_allowance: int = 0,
        watchdog_reason: str | None = None,
        recovery_scope: RecoveryScope | str | None = None,
    ) -> TaskPolicyResult:
        """Atomically admit logical units before dispatch."""

        _positive_int(requested_units, "requested_units")
        return self._decide(
            requested_units=requested_units,
            resource=resource,
            token_allowance=token_allowance,
            watchdog_reason=watchdog_reason,
            recovery_scope=recovery_scope,
            consume=True,
        )

    def authorize_recovery(
        self, scope: RecoveryScope | str, amount: int = 1
    ) -> TaskPolicyResult:
        """Delegate recovery consumption to its existing atomic owner."""

        return authorize_recovery(self, scope, amount)

    def to_checkpoint_dict(self) -> dict[str, Any]:
        return self.state.to_checkpoint_dict(self._clock())

    def restore_checkpoint(self, raw: Mapping[str, Any]) -> None:
        self.state.restore_checkpoint(raw, maximum=self._limit("max_steps"))

    def _decide(
        self,
        *,
        requested_units: int,
        resource: str | None,
        token_allowance: int,
        watchdog_reason: str | None,
        recovery_scope: RecoveryScope | str | None,
        consume: bool,
    ) -> TaskPolicyResult:
        return decide(
            self,
            requested_units=requested_units,
            resource=resource,
            token_allowance=token_allowance,
            watchdog_reason=watchdog_reason,
            recovery_scope=recovery_scope,
            consume=consume,
        )

    def _limit(self, name: str) -> int:
        value = self.limits.get(name) if isinstance(self.limits, Mapping) else getattr(self.limits, name, None)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
        return value

    def _result(self, decision: TaskPolicyDecision, **kwargs: Any) -> TaskPolicyResult:
        return TaskPolicyResult(decision=decision, **kwargs)

    def _publish(self, result: TaskPolicyResult) -> TaskPolicyResult:
        return publish(self, result)


def _positive_int(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def resolve_task_policy(
    limits: Any,
    *,
    policy_state: TaskPolicyState | None,
    policy: TaskRuntimePolicy | None,
    budget_ledger: Any,
    recovery_budget: Any,
    cancellation: Any,
    event_sink: Any,
    correlation: Any,
) -> tuple[TaskPolicyState, TaskRuntimePolicy]:
    state = policy_state
    if state is None and policy is not None:
        state = policy.state
    if state is None:
        state = TaskPolicyState()
    if policy is None:
        policy = TaskRuntimePolicy(
            limits,
            state=state,
            budget_ledger=budget_ledger,
            recovery_budget=recovery_budget,
            cancellation=cancellation,
            event_sink=event_sink,
            correlation=correlation,
        )
    elif getattr(policy, "_correlation", None) is None:
        policy.set_correlation(correlation)
    return state, policy


def bind_task_execution_context(context: Any, *, ledger: Any, correlation: Any) -> None:
    state, policy = resolve_task_policy(
        context.limits,
        policy_state=context.policy_state,
        policy=context.task_policy,
        budget_ledger=ledger,
        recovery_budget=context.recovery_budget,
        cancellation=context.cancellation,
        event_sink=context.event_sink,
        correlation=correlation,
    )
    canonical_state = getattr(policy, "state", state)
    if canonical_state is not state:
        state = canonical_state
    policy_ledger = getattr(policy, "budget_ledger", None)
    if policy_ledger is None:
        policy.budget_ledger = ledger
        policy_ledger = ledger
    if policy_ledger is not None and getattr(context, "budget_ledger", None) is not policy_ledger:
        object.__setattr__(context, "budget_ledger", policy_ledger)
        object.__setattr__(context, "model_call_budget", policy_ledger)
    policy_recovery = getattr(policy, "recovery_budget", None)
    if policy_recovery is None:
        policy.recovery_budget = context.recovery_budget
        policy_recovery = context.recovery_budget
    if policy_recovery is not None and context.recovery_budget is not policy_recovery:
        object.__setattr__(context, "recovery_budget", policy_recovery)
    policy_cancellation = getattr(policy, "cancellation", None)
    if policy_cancellation is None:
        policy.cancellation = context.cancellation
        policy_cancellation = context.cancellation
    if policy_cancellation is not context.cancellation:
        object.__setattr__(context, "cancellation", policy_cancellation)
    object.__setattr__(context, "policy_state", state)
    object.__setattr__(context, "task_policy", policy)


PolicyDecision = TaskPolicyDecision
PolicyResult = TaskPolicyResult
TaskPolicy = TaskRuntimePolicy


__all__ = [
    "PolicyDecision",
    "PolicyResult",
    "TaskPolicy",
    "TaskPolicyDecision",
    "TaskPolicyError",
    "TaskPolicyResult",
    "TaskPolicyState",
    "TaskRuntimePolicy",
    "resolve_task_policy",
]
