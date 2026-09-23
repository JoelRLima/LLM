"""Bounded completed-run inspection backend."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.engineering.contracts import (
    EngineeringBackendOutcome,
    EngineeringBackendProtocolError,
    EngineeringBackendStatus,
    EngineeringExecutionContext,
    EngineeringRequest,
)
from agent.engineering.summary import normalize_summary
from agent.observability.bookmarks import BookmarkStore
from agent.presentation import InspectionService

_PROJECTION_KEYS = (
    "schema_version",
    "run",
    "current",
    "plan_steps",
    "timeline",
    "tools",
    "validation",
    "recovery",
    "changes",
    "metrics",
    "warnings",
    "heartbeat",
    "issues",
    "convergence",
)

class InspectionBackend:
    def __init__(self, workspace_paths: object | None = None) -> None:
        self._workspace_paths = workspace_paths

    def execute(self, request: EngineeringRequest, context: EngineeringExecutionContext) -> EngineeringBackendOutcome:
        trace_run_id = request.parameters.get("trace_run_id")
        limit = request.parameters.get("limit")
        if not isinstance(trace_run_id, str) or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 64:
            return EngineeringBackendOutcome(EngineeringBackendStatus.FAILED, {"status": "invalid_parameters"}, (), False, False)
        try:
            projection = self.inspect(trace_run_id, limit, context)
        except (TypeError, ValueError) as exc:
            raise EngineeringBackendProtocolError("inspection projection is invalid or exceeds its bound") from exc
        if projection is None:
            return EngineeringBackendOutcome(EngineeringBackendStatus.FAILED, {"status": "not_found"}, (), False, False)
        return EngineeringBackendOutcome(
            EngineeringBackendStatus.SUCCEEDED,
            projection,
            (),
            False,
            False,
        )

    def inspect(self, trace_run_id: str, limit: int, context: EngineeringExecutionContext) -> dict[str, object] | None:
        if context.workspace is None:
            return None
        workspace_paths = self._workspace_paths or context.app_paths.for_workspace(context.workspace.workspace_id)
        try:
            service = InspectionService(
                workspace_paths,
                canonical_reader=None,
                silence_reader=None,
                bookmark_reader=BookmarkStore(workspace_paths).reader,
            )
            snapshot = service.snapshot(trace_run_id, limit=limit)
            raw = snapshot.to_dict()
        except (OSError, RuntimeError, ValueError, KeyError):
            return None
        projected: dict[str, object] = {"schema_version": 1}
        for key in _PROJECTION_KEYS:
            value = raw.get(key)
            if key in {"timeline", "warnings"} and isinstance(value, list):
                projected[key] = value[:limit]
            elif isinstance(value, dict):
                projected[key] = _bounded_nested(value, limit=limit)
            elif isinstance(value, list):
                projected[key] = value[:64]
            else:
                projected[key] = value
        try:
            return normalize_summary(projected)
        except (TypeError, ValueError):
            raise


def _bounded_nested(value: dict[str, object], *, limit: int) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(item, list):
            result[key] = item[:min(limit, 64)]
        elif isinstance(item, dict):
            result[key] = _bounded_nested(item, limit=limit)
        else:
            result[key] = item
    return result


def project_inspection(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "schema_version", "run", "current", "plan_steps", "timeline", "tools",
        "validation", "recovery", "changes", "metrics", "warnings", "heartbeat",
        "issues", "convergence",
    }
    return {key: _bounded_model_safe_json(item) for key, item in value.items() if key in allowed}


def _bounded_model_safe_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return None
    if isinstance(value, Mapping):
        return {
            str(key): _bounded_model_safe_json(item, depth=depth + 1)
            for key, item in list(value.items())[:64]
            if isinstance(key, str) and len(key) <= 512
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_model_safe_json(item, depth=depth + 1) for item in list(value)[:64]]
    if isinstance(value, str):
        return value[:512]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return None


__all__ = ["InspectionBackend", "project_inspection"]
