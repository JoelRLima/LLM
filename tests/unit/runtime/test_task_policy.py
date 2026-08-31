from __future__ import annotations

import copy
from types import SimpleNamespace

from agent.cancellation import CancellationToken
from agent.runtime.budget import TaskBudgetLedger
from agent.runtime.context import RuntimeLimits, TaskExecutionContext
from agent.runtime.model_call_support import context_for_session
from agent.runtime.recovery import RecoveryBudgetState, RecoveryScope
from agent.runtime.task_policy import (
    TaskPolicyDecision,
    TaskPolicyState,
    TaskRuntimePolicy,
)


def _limits(**overrides: int) -> RuntimeLimits:
    return RuntimeLimits(
        max_steps=overrides.get("max_steps", 10),
        max_task_wall_seconds=overrides.get("max_task_wall_seconds", 100),
        max_model_calls=overrides.get("max_model_calls", 10),
        max_task_tool_calls=overrides.get("max_task_tool_calls", 10),
        max_task_tokens=overrides.get("max_task_tokens", 10_000),
    )


def _policy(
    *,
    limits: RuntimeLimits | None = None,
    clock: list[float] | None = None,
    token: CancellationToken | None = None,
    ledger: TaskBudgetLedger | None = None,
    state: TaskPolicyState | None = None,
    recovery: RecoveryBudgetState | None = None,
) -> TaskRuntimePolicy:
    return TaskRuntimePolicy(
        limits or _limits(),
        state=state,
        budget_ledger=ledger,
        recovery_budget=recovery,
        cancellation=token or CancellationToken(),
        clock=(lambda: clock[0]) if clock is not None else None,
    )


def test_step_admission_has_exact_n_and_n_plus_one_boundary() -> None:
    policy = _policy(limits=_limits(max_steps=2))

    assert policy.admit_work_units().decision is TaskPolicyDecision.ALLOW
    assert policy.admit_work_units().decision is TaskPolicyDecision.ALLOW
    refused = policy.admit_work_units()

    assert refused.decision is TaskPolicyDecision.LOGICAL_STEP_EXHAUSTED
    assert policy.logical_work_units_consumed == 2


def test_batch_admission_is_atomic_and_truncated_to_remaining_logical_units() -> None:
    policy = _policy(limits=_limits(max_steps=2))
    policy.admit_work_units()

    result = policy.admit_work_units(4)

    assert result.decision is TaskPolicyDecision.TRUNCATE_BATCH
    assert result.admitted_units == 1
    assert policy.logical_work_units_consumed == 2


def test_policy_reads_quantitative_ledger_without_creating_a_second_budget() -> None:
    ledger = TaskBudgetLedger(max_model_calls=4, max_task_tool_calls=2, max_task_tokens=100)
    ledger.reserve_tool_call()
    policy = _policy(ledger=ledger)

    result = policy.admit_work_units(resource="tool_calls")

    assert result.allowed
    assert ledger.snapshot().tool_calls == 1
    assert policy.logical_work_units_consumed == 1


def test_active_elapsed_checkpoint_excludes_downtime() -> None:
    clock = [10.0]
    state = TaskPolicyState()
    policy = _policy(
        limits=_limits(max_task_wall_seconds=10),
        clock=clock,
        state=state,
    )
    policy.start_active_segment()
    clock[0] = 13.0
    policy.pause_active_segment()
    checkpoint = state.to_checkpoint_dict()

    clock[0] = 1_000.0
    restored = TaskPolicyState()
    restored.restore_checkpoint(checkpoint)
    resumed = _policy(
        limits=_limits(max_task_wall_seconds=10),
        clock=clock,
        state=restored,
    )
    resumed.start_active_segment()
    clock[0] = 1_004.0

    assert resumed.active_elapsed_seconds == 7.0
    assert resumed.check_current().decision is TaskPolicyDecision.ALLOW


def test_cancellation_precedes_simultaneous_quantitative_and_logical_exhaustion() -> None:
    token = CancellationToken()
    token.cancel()
    ledger = TaskBudgetLedger(max_model_calls=1, max_task_tool_calls=1, max_task_tokens=1)
    policy = _policy(limits=_limits(max_steps=0), token=token, ledger=ledger)

    result = policy.admit_work_units(resource="tool_calls")

    assert result.decision is TaskPolicyDecision.CANCELLED
    assert result.terminal_status == "cancelled"


def test_context_child_reuses_canonical_cancellation_and_policy_state() -> None:
    token = CancellationToken()
    context = TaskExecutionContext(
        model_gateway=SimpleNamespace(),
        cancellation=token,
        limits=_limits(),
    )
    child = context.child("node-1")

    assert context.task_policy.cancellation is token
    assert child.cancellation is token
    assert child.task_policy is context.task_policy
    assert child.policy_state is context.policy_state


def test_model_session_context_reuses_policy_cancellation_authority() -> None:
    policy_token = CancellationToken()
    session_token = CancellationToken()
    policy = _policy(token=policy_token)
    session = SimpleNamespace(
        config={},
        model_profile=SimpleNamespace(model="test", provider="test"),
        gateway=SimpleNamespace(model="test"),
        cancellation_token=session_token,
        task_policy=policy,
        budget_ledger=TaskBudgetLedger(max_model_calls=10, max_task_tool_calls=10, max_task_tokens=1000),
        event_sink=None,
    )

    context = context_for_session(session)

    assert context.cancellation is policy_token
    assert session.cancellation_token is policy_token


def test_recovery_authority_remains_bounded_and_checkpointable() -> None:
    recovery = RecoveryBudgetState()
    policy = _policy(recovery=recovery)

    first = policy.authorize_recovery(RecoveryScope.STRUCTURED_RESPONSE_REPAIRS)
    second = policy.authorize_recovery(RecoveryScope.STRUCTURED_RESPONSE_REPAIRS)
    restored = RecoveryBudgetState()
    restored.restore_snapshot(recovery.to_checkpoint_dict())

    assert first.allowed
    assert second.decision is TaskPolicyDecision.RECOVERY_EXHAUSTED
    assert restored.used(RecoveryScope.STRUCTURED_RESPONSE_REPAIRS) == 1


def test_policy_state_is_safe_to_deepcopy_for_transactional_checkpoint_restore() -> None:
    state = TaskPolicyState()
    policy = _policy(state=state)
    policy.admit_work_units(2)

    staged = copy.deepcopy(state)

    assert staged.logical_work_units_consumed == 2
    assert staged.to_checkpoint_dict()["active_elapsed_seconds"] == 0.0
