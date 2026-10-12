"""Application composition for the standalone inspection read surface."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from llm_agent.agent.observability.trace_store import TraceCorruptError, TraceUnavailableError
from llm_agent.agent.presentation.query import InspectionQuery
from llm_agent.agent.presentation.service import InspectionService
from llm_agent.application.context import WorkspacePaths
from llm_agent.application.inspection.contracts import InspectionListRequest, InspectionReadRequest
from llm_agent.application.inspection.errors import InspectionCorruptDataError, InspectionUnavailableError


class InspectionReadContext:
    """Application-owned lifetime context for standalone inspection reads."""

    __slots__ = ("_service",)

    def __init__(self, workspace_paths: WorkspacePaths) -> None:
        self._service = InspectionService(workspace_paths)


def read_inspection(
    context: InspectionReadContext,
    request: InspectionReadRequest,
) -> Mapping[str, Any]:
    """Execute one current replay or snapshot read and return its primitive projection."""

    try:
        return _read_inspection(context, request)
    except TraceUnavailableError as exc:
        raise InspectionUnavailableError(str(exc)) from exc
    except TraceCorruptError as exc:
        raise InspectionCorruptDataError(str(exc)) from exc


def _read_inspection(
    context: InspectionReadContext,
    request: InspectionReadRequest,
) -> Mapping[str, Any]:
    query = InspectionQuery.build(
        sequence_start=request.sequence_start,
        sequence_end=request.sequence_end,
        sources=request.sources,
        event_kinds=request.event_kinds,
        activity_categories=request.activity_categories,
        severities=request.severities,
        statuses=request.statuses,
        task_id=request.task_id,
        root_task_id=request.root_task_id,
        step=request.step,
        correlation_id=request.correlation_id,
        invocation_id=request.invocation_id,
        time_start=request.time_start,
        time_end=request.time_end,
        search=request.search,
        bookmarked_only=request.bookmarked_only,
    )
    service = context._service
    after_sequence = request.after_sequence
    if request.mode == "replay":
        selected = service.select(request.run_id)
        activities = service.replay(
            selected.metadata.run_id,
            query=query,
            limit=request.limit,
            after_sequence=after_sequence,
        )
        return {
            "run": selected.metadata.to_dict(),
            "completeness": selected.read_result.completeness.value,
            "issues": list(selected.read_result.issues),
            "activities": [item.to_dict() for item in activities],
        }
    snapshot = service.snapshot(
        request.run_id,
        query=query,
        limit=request.limit,
        selected_sequence=request.selected_sequence,
        after_sequence=after_sequence,
    )
    return cast("Mapping[str, Any]", snapshot.to_dict())


def list_inspection_runs(
    context: InspectionReadContext,
    request: InspectionListRequest,
) -> tuple[Mapping[str, Any], ...]:
    """List retained runs as canonical primitive projections."""

    try:
        return tuple(run.to_dict() for run in context._service.list_runs(limit=request.limit))
    except TraceUnavailableError as exc:
        raise InspectionUnavailableError(str(exc)) from exc
    except TraceCorruptError as exc:
        raise InspectionCorruptDataError(str(exc)) from exc


__all__ = ["InspectionReadContext", "read_inspection", "list_inspection_runs"]
