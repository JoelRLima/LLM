"""Small public-safe projections used by run receipt construction."""

from __future__ import annotations

from typing import Any

from agent.execution_incidents import CANONICAL_COMMIT_FAILED
from agent.reporting.metrics import RunMetricsSnapshot, project_run_metrics
from agent.reporting.public_safety import sanitize_public_text
from agent.runtime.outcome_taxonomy import failure_layer_for_code as _canonical_failure_layer_for_code


def failure_layer_for_code(code: str | None) -> str:
    return _canonical_failure_layer_for_code(code)


def execution_incidents(state: Any) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in (getattr(state, "execution_incidents", None) or [])
        if isinstance(item, dict)
    ]


def executed_projection(
    effects: list[bool], incidents: list[dict[str, Any]]
) -> bool | None:
    incident_effects = [
        item.get("executed")
        for item in incidents
        if type(item.get("executed")) is bool
    ]
    values = effects or incident_effects
    if len(values) == 1:
        return values[-1]
    return any(values) if values else None


def receipt_cause(
    error: str | None,
    code: str | None,
    last_result: dict[str, Any],
    failure_layer: str | None,
) -> dict[str, Any] | None:
    if not error and not code:
        return None
    message = error or str(last_result.get("error") or "")
    if not message and code == CANONICAL_COMMIT_FAILED:
        message = "O commit canonico do estado falhou."
    return {
        "message": sanitize_public_text(message),
        "code": code or "RUN_FAILED",
        "layer": failure_layer or failure_layer_for_code(code),
    }


def metrics_for_orchestrator(
    orchestrator: Any,
    *,
    snapshot: RunMetricsSnapshot | None = None,
) -> dict[str, Any] | None:
    if snapshot is None:
        selected = metrics_snapshot_for_orchestrator(orchestrator)
    else:
        selected = snapshot
    return selected.to_dict() if selected is not None else None


def metrics_snapshot_for_orchestrator(
    orchestrator: Any, *, snapshot: RunMetricsSnapshot | None = None
) -> RunMetricsSnapshot | None:
    if snapshot is not None:
        return snapshot
    if snapshot is None:
        getter = getattr(orchestrator, "_get_metrics_for_task", None)
        if not callable(getter):
            return None
        entries = getter()
        ledger = getattr(orchestrator, "task_budget", None)
        budget_snapshot = ledger.snapshot() if ledger is not None and hasattr(ledger, "snapshot") else None
        history = getattr(getattr(orchestrator, "agent_state", None), "tool_history", ()) or ()
        return project_run_metrics(
            entries,
            tool_calls=(budget_snapshot.tool_calls if budget_snapshot is not None else None),
            history_records=len(history),
            budget_snapshot=budget_snapshot,
        )
    return snapshot


__all__ = [
    "executed_projection",
    "execution_incidents",
    "failure_layer_for_code",
    "metrics_for_orchestrator",
    "metrics_snapshot_for_orchestrator",
    "receipt_cause",
]
