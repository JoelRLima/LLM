"""Decision mechanics behind the public task-policy seam."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from agent.cancellation import is_cancellation_requested
from agent.runtime.event_dispatch import dispatch_runtime_event
from agent.runtime.events import RuntimeEvent
from agent.runtime.recovery import RecoveryScope
from agent.runtime.task_policy_types import TaskPolicyDecision, TaskPolicyResult


def decide(
    owner: Any,
    *,
    requested_units: int,
    resource: str | None,
    token_allowance: int,
    watchdog_reason: str | None,
    recovery_scope: RecoveryScope | str | None,
    consume: bool,
) -> TaskPolicyResult:
    _non_negative_int(requested_units, "requested_units")
    _non_negative_int(token_allowance, "token_allowance")
    with owner.state.atomic():
        result = _check_without_logical(
            owner,
            requested_units=requested_units,
            resource=resource,
            token_allowance=token_allowance,
            watchdog_reason=watchdog_reason,
            recovery_scope=recovery_scope,
        )
        if result is not None:
            return _publish_result(owner, result)
        if not consume or requested_units == 0:
            return _publish_result(owner, _owner_result(owner, TaskPolicyDecision.ALLOW))

        maximum = _limit(owner, "max_steps")
        remaining = owner.state.remaining_logical_work_units(maximum)
        quantitative_remaining, _ = quantitative_remaining_for(owner, resource, token_allowance)
        admitted = owner.state.consume_logical_work_units(
            min(requested_units, quantitative_remaining), maximum
        )
        if admitted <= 0:
            return _publish_result(owner,
                _owner_result(owner,
                    TaskPolicyDecision.LOGICAL_STEP_EXHAUSTED,
                    requested_units=requested_units,
                    remaining_units=0,
                    message="O limite de unidades lógicas da tarefa foi atingido.",
                )
            )
        if admitted < requested_units:
            reason = (
                "TASK_QUANTITATIVE_BUDGET_TRUNCATED"
                if quantitative_remaining_for(owner, resource, token_allowance)[0] < requested_units
                else "TASK_LOGICAL_STEP_TRUNCATED"
            )
            return _publish_result(owner,
                _owner_result(owner,
                    TaskPolicyDecision.TRUNCATE_BATCH,
                    requested_units=requested_units,
                    admitted_units=admitted,
                    remaining_units=remaining - admitted,
                    reason_code=reason,
                    message=(
                        f"A admissão foi truncada para {admitted} unidade(s) "
                        "dentro do orçamento restante da tarefa."
                    ),
                )
            )
        return _publish_result(owner,
            _owner_result(owner,
                TaskPolicyDecision.ALLOW,
                requested_units=requested_units,
                admitted_units=admitted,
                remaining_units=remaining - admitted,
            )
        )


def authorize_recovery(owner: Any, scope: RecoveryScope | str, amount: int) -> TaskPolicyResult:
    _positive_int(amount, "amount")
    with owner.state.atomic():
        preflight = _check_without_logical(
            owner,
            requested_units=0,
            resource=None,
            token_allowance=0,
            watchdog_reason=None,
            recovery_scope=scope,
        )
        if preflight is not None:
            return _publish_result(owner, preflight)
        if _cancelled(owner):
            return _publish_result(owner,
                _owner_result(owner,
                    TaskPolicyDecision.CANCELLED,
                    requested_units=amount,
                    message="A tarefa foi cancelada.",
                )
            )
        budget = owner.recovery_budget
        if budget is None:
            return _publish_result(owner,
                _owner_result(
                    owner,
                    TaskPolicyDecision.RECOVERY_EXHAUSTED,
                    requested_units=amount,
                    reason_code="TASK_RECOVERY_OWNER_MISSING",
                    message="A recuperação foi bloqueada porque não há RecoveryBudgetState proprietário.",
                )
            )
        if not budget.try_consume(scope, amount):
            return _publish_result(owner,
                _owner_result(owner,
                    TaskPolicyDecision.RECOVERY_EXHAUSTED,
                    requested_units=amount,
                    message=f"O limite de recuperação para {scope} foi atingido.",
                )
            )
        return _publish_result(owner,
            _owner_result(owner,
                TaskPolicyDecision.ALLOW,
                requested_units=amount,
                admitted_units=amount,
            )
        )


def check_without_logical(
    owner: Any,
    *,
    requested_units: int,
    resource: str | None,
    token_allowance: int,
    watchdog_reason: str | None,
    recovery_scope: RecoveryScope | str | None,
) -> TaskPolicyResult | None:
    return _check_without_logical(
        owner,
        requested_units=requested_units,
        resource=resource,
        token_allowance=token_allowance,
        watchdog_reason=watchdog_reason,
        recovery_scope=recovery_scope,
    )


def quantitative_remaining_for(owner: Any, resource: str | None, token_allowance: int) -> tuple[int, str]:
    ledger = owner.budget_ledger
    if ledger is None:
        return 2**63 - 1, "task"
    normalized = (resource or "").lower()
    if normalized in {"model", "models", "model_call", "model_calls"}:
        remaining, kind = ledger.remaining_model_calls, "model_calls"
    elif normalized in {"tool", "tools", "tool_call", "tool_calls", "io"}:
        remaining, kind = ledger.remaining_tool_calls, "tool_calls"
    elif normalized in {"token", "tokens", "task_tokens"}:
        remaining, kind = ledger.remaining_task_tokens, "task_tokens"
    else:
        remaining, kind = 2**63 - 1, "task"
    if token_allowance > ledger.remaining_task_tokens or ledger.remaining_task_tokens == 0:
        return 0, "task_tokens"
    return remaining, kind


def _check_without_logical(
    owner: Any,
    *,
    requested_units: int,
    resource: str | None,
    token_allowance: int,
    watchdog_reason: str | None,
    recovery_scope: RecoveryScope | str | None,
) -> TaskPolicyResult | None:
    if _cancelled(owner):
        return _owner_result(owner,
            TaskPolicyDecision.CANCELLED,
            requested_units=requested_units,
            message="A tarefa foi cancelada antes da admissão.",
        )
    quantitative_remaining, quantitative_kind = quantitative_remaining_for(owner, resource, token_allowance)
    if quantitative_remaining == 0 and (requested_units > 0 or token_allowance > 0 or resource is not None):
        return _owner_result(owner,
            TaskPolicyDecision.QUANTITATIVE_EXHAUSTED,
            requested_units=requested_units,
            remaining_units=0,
            reason_code="TASK_QUANTITATIVE_BUDGET_EXHAUSTED",
            message=f"O orçamento quantitativo de {quantitative_kind} foi esgotado.",
        )
    if requested_units > 0 and owner.state.remaining_logical_work_units(_limit(owner, "max_steps")) == 0:
        return _owner_result(owner,
            TaskPolicyDecision.LOGICAL_STEP_EXHAUSTED,
            requested_units=requested_units,
            remaining_units=0,
            message="O limite de unidades lógicas da tarefa foi atingido.",
        )
    if owner.active_elapsed_seconds >= float(_limit(owner, "max_task_wall_seconds")):
        return _owner_result(owner,
            TaskPolicyDecision.ACTIVE_WALL_EXHAUSTED,
            requested_units=requested_units,
            remaining_units=owner.remaining_work_units(),
            message="O tempo ativo cumulativo da tarefa foi esgotado.",
        )
    watchdog_decision = _watchdog_decision(watchdog_reason)
    if watchdog_decision is not None:
        return _owner_result(owner,
            watchdog_decision,
            requested_units=requested_units,
            remaining_units=owner.remaining_work_units(),
            message=watchdog_reason or "O watchdog interrompeu a tarefa.",
        )
    if recovery_scope is not None:
        if owner.recovery_budget is None:
            return _owner_result(
                owner,
                TaskPolicyDecision.RECOVERY_EXHAUSTED,
                requested_units=requested_units,
                remaining_units=owner.remaining_work_units(),
                reason_code="TASK_RECOVERY_OWNER_MISSING",
                message="A recuperação foi bloqueada porque não há RecoveryBudgetState proprietário.",
            )
        if not owner.recovery_budget.can_attempt(recovery_scope):
            return _owner_result(owner,
                TaskPolicyDecision.RECOVERY_EXHAUSTED,
                requested_units=requested_units,
                remaining_units=owner.remaining_work_units(),
                message=f"O limite de recuperação para {recovery_scope} foi atingido.",
            )
    return None


def _watchdog_decision(reason: str | None) -> TaskPolicyDecision | None:
    if not reason:
        return None
    normalized = reason.casefold()
    if "loop sem progresso" in normalized or "no progress" in normalized:
        return TaskPolicyDecision.WATCHDOG_NO_PROGRESS
    if "falha" in normalized or "error" in normalized or "failure" in normalized:
        return TaskPolicyDecision.WATCHDOG_REPEATED_ERROR
    if "timeout" in normalized or "tempo" in normalized:
        return TaskPolicyDecision.ACTIVE_WALL_EXHAUSTED
    return TaskPolicyDecision.WATCHDOG_REPEATED_ERROR


def _limit(owner: Any, name: str) -> int:
    value = owner.limits.get(name) if isinstance(owner.limits, Mapping) else getattr(owner.limits, name, None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _cancelled(owner: Any) -> bool:
    return is_cancellation_requested(owner.cancellation)


def _publish(owner: Any, result: TaskPolicyResult) -> TaskPolicyResult:
    if owner._event_sink is None or owner._correlation is None:
        return result
    try:
        event = RuntimeEvent.from_fields("task_policy_decision", owner._correlation, result.to_dict())
        dispatch_runtime_event(owner._event_sink, event)
    except Exception:
        pass
    return result


def _publish_result(owner: Any, result: TaskPolicyResult) -> TaskPolicyResult:
    return cast(TaskPolicyResult, owner._publish(result))


def _owner_result(owner: Any, decision: TaskPolicyDecision, **kwargs: Any) -> TaskPolicyResult:
    return cast(TaskPolicyResult, owner._result(decision, **kwargs))


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


__all__ = ["authorize_recovery", "check_without_logical", "decide", "quantitative_remaining_for"]
