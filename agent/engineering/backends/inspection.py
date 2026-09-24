"""Bounded completed-run inspection backend."""

from __future__ import annotations

from agent.engineering.backends.inspection_projection import project_inspection
from agent.engineering.contracts import (
    EngineeringBackendOutcome,
    EngineeringBackendProtocolError,
    EngineeringBackendStatus,
    EngineeringExecutionContext,
    EngineeringRequest,
)
from agent.observability.bookmarks import BookmarkStore
from agent.presentation import InspectionService


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
        return project_inspection(raw, limit=limit)


__all__ = ["InspectionBackend", "project_inspection"]
