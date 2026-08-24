"""Small public-safe projections used by run receipt construction."""

from __future__ import annotations

from typing import Any

from agent.execution_incidents import CANONICAL_COMMIT_FAILED
from agent.reporting.public_safety import sanitize_public_text


def failure_layer_for_code(code: str | None) -> str:
    if code == "MODEL_PROVIDER_ERROR":
        return "provider"
    if code in {
        "APPLICATION_AUTHORITY_MISSING",
        "APPLICATION_AUTHORITY_DENIED",
        "TASK_AUTHORITY_MISSING",
        "TASK_AUTHORITY_DENIED",
        "WORKSPACE_GRANT_DENIED",
        "RUNTIME_MISMATCH",
        "APPROVAL_REQUIRED",
        "APPROVAL_DENIED",
        "PERMISSION_DENIED",
    }:
        return "gateway"
    if code in {"TOOL_ERROR", "EXECUTION_ERROR", "TOOL_NOT_FOUND"}:
        return "tool"
    return "runtime"


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


__all__ = [
    "executed_projection",
    "execution_incidents",
    "failure_layer_for_code",
    "receipt_cause",
]
