"""Fail-closed reconciliation helpers for public report projections."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.runtime.operational_outcome import (
    local_failure_permitted,
    normalize_terminal_status,
    project_operational_outcome,
)


def reconcile_report_status(state: Any, requested_status: str) -> str:
    last = getattr(state, "last_result", None)
    last_status = last.get("status") if isinstance(last, Mapping) else None
    disposition = getattr(state, "terminal_disposition", None)
    task_failed = bool(getattr(state, "_task_failed", False))
    cancelled = bool(getattr(state, "_cancelled", False))
    return normalize_terminal_status(
        explicit_status=requested_status,
        last_result_status=last_status,
        terminal_disposition=disposition,
        task_failed=task_failed,
        cancelled=cancelled,
        local_failure_permitted=local_failure_permitted(state),
    )


def canonical_effect_projection(state: Any, status: str) -> dict[str, Any]:
    outcome = project_operational_outcome(
        state,
        terminal_status=status,
        task_failed=bool(getattr(state, "_task_failed", False)),
        cancelled=bool(getattr(state, "_cancelled", False)),
    )
    return {
        "operational_outcome": outcome.to_dict(),
        "files_affected": list(outcome.files_affected),
        "mutation_occurred": outcome.mutation_occurred,
        "final_state": outcome.final_state,
    }


def reconcile_receipt_projection(
    state: Any, status: str, receipt: dict[str, Any]
) -> dict[str, Any]:
    status = reconcile_report_status(state, status)
    projection = dict(receipt)
    projection["status"] = status
    effects = canonical_effect_projection(state, status)
    projection.update({key: value for key, value in effects.items() if key != "operational_outcome"})
    projection["operational_outcome"] = effects["operational_outcome"]
    return projection


__all__ = [
    "canonical_effect_projection",
    "reconcile_receipt_projection",
    "reconcile_report_status",
]
