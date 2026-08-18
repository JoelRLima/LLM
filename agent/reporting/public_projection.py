"""Fail-closed reconciliation helpers for public report projections."""

from __future__ import annotations

from typing import Any

from agent.reporting.operational_outcome import normalize_terminal_status, project_operational_outcome


def reconcile_report_status(state: Any, requested_status: str) -> str:
    last = getattr(state, "last_result", None)
    last_status = last.get("status") if isinstance(last, dict) else None
    disposition = getattr(state, "terminal_disposition", None)
    task_failed = bool(getattr(state, "_task_failed", False))
    cancelled = bool(getattr(state, "_cancelled", False))
    if disposition is None and last_status is None and not task_failed and not cancelled:
        return requested_status
    return normalize_terminal_status(
        explicit_status=requested_status,
        last_result_status=last_status,
        terminal_disposition=disposition,
        task_failed=task_failed,
        cancelled=cancelled,
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
        "final_state": (
            "restored"
            if outcome.rollback_occurred
            else ("applied" if outcome.mutation_occurred else None)
        ),
    }


def reconcile_receipt_projection(
    state: Any, status: str, receipt: dict[str, Any]
) -> dict[str, Any]:
    projection = dict(receipt)
    projection["status"] = status
    effects = canonical_effect_projection(state, status)
    has_facts = (
        getattr(state, "terminal_disposition", None) is not None
        or isinstance(getattr(state, "last_result", None), dict)
        or bool(getattr(state, "_task_failed", False))
        or bool(getattr(state, "_cancelled", False))
    )
    if has_facts:
        projection.update({key: value for key, value in effects.items() if key != "operational_outcome"})
        nested = effects["operational_outcome"]
    else:
        nested = projection.get("operational_outcome")
    if isinstance(nested, dict):
        nested = dict(nested)
        nested["terminal_status"] = status
        projection["operational_outcome"] = nested
    return projection


__all__ = [
    "canonical_effect_projection",
    "reconcile_receipt_projection",
    "reconcile_report_status",
]
