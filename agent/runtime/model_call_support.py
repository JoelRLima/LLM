"""Construction helpers for the canonical model-call context."""

from __future__ import annotations

from typing import Any, cast

from agent.cancellation import CancellationToken
from agent.runtime.context import RuntimeLimits, TaskExecutionContext
from agent.runtime.model_call_record import SessionMetricsSink


def context_for_session(session: Any) -> TaskExecutionContext:
    config = getattr(session, "config", {})
    profile = getattr(session, "model_profile", None)
    metadata = {
        "model": getattr(profile, "model", getattr(session.gateway, "model", None)),
        "provider": getattr(profile, "provider", None),
    }
    policy = getattr(session, "task_policy", None)
    cancellation = getattr(policy, "cancellation", None)
    if cancellation is None:
        cancellation = getattr(session, "cancellation_token", None)
    if cancellation is None:
        cancellation = CancellationToken()
    if policy is not None and getattr(policy, "cancellation", None) is None:
        policy.cancellation = cancellation
    if getattr(session, "cancellation_token", None) is not cancellation:
        session.cancellation_token = cancellation
    canonical_cancellation = cast(CancellationToken, cancellation)
    return TaskExecutionContext(
        model_gateway=session.gateway,
        cancellation=canonical_cancellation,
        model_profile=profile,
        limits=RuntimeLimits.from_config(config),
        correlation=getattr(session, "run_correlation", None),
        event_sink=getattr(session, "event_sink", None) or None,
        metrics_sink=SessionMetricsSink(session),
        budget_ledger=session.budget_ledger,
        policy_state=getattr(policy, "state", None),
        recovery_budget=getattr(policy, "recovery_budget", None),
        task_policy=policy,
        metadata=metadata,
    )


__all__ = ["context_for_session"]
