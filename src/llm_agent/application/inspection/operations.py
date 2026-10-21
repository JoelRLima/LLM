"""Workspace-scoped bookmark and diagnostic-export use cases."""

from __future__ import annotations

from llm_agent.agent.observability.bookmarks import Bookmark, BookmarkStore
from llm_agent.agent.observability.export import DiagnosticExporter
from llm_agent.agent.observability.trace_store import TraceCorruptError, TraceUnavailableError
from llm_agent.agent.presentation.service import InspectionService
from llm_agent.application.context import WorkspacePaths
from llm_agent.application.inspection.contracts import (
    BookmarkAddRequest,
    BookmarkAddResult,
    BookmarkListRequest,
    BookmarkListResult,
    BookmarkRemoveRequest,
    BookmarkRemoveResult,
    BookmarkView,
    DiagnosticExportRequest,
    DiagnosticExportResult,
)
from llm_agent.application.inspection.errors import (
    InspectionCorruptDataError,
    InspectionUnavailableError,
)


class InspectionAuxiliaryOperations:
    """Coordinate explicit bookmark/export operations for one workspace."""

    __slots__ = ("_bookmarks", "_exporter", "_inspection")

    def __init__(self, workspace_paths: WorkspacePaths) -> None:
        self._inspection = InspectionService(workspace_paths)
        self._bookmarks = BookmarkStore(workspace_paths)
        self._exporter = DiagnosticExporter(self._inspection)

    def list_bookmarks(self, request: BookmarkListRequest) -> BookmarkListResult:
        try:
            run_id = self._selected_bookmark_run(request.run_id)
            return BookmarkListResult(
                tuple(self._bookmark_view(item) for item in self._bookmarks.list(run_id))
            )
        except TraceCorruptError as exc:
            raise InspectionCorruptDataError(str(exc)) from exc
        except TraceUnavailableError as exc:
            raise InspectionUnavailableError(str(exc)) from exc

    def add_bookmark(self, request: BookmarkAddRequest) -> BookmarkAddResult:
        if request.sequence is None:
            raise ValueError("bookmark add requer --sequence")
        try:
            run_id = self._selected_bookmark_run(request.run_id)
            bookmark = self._bookmarks.add(run_id, request.sequence, request.note)
            return BookmarkAddResult(self._bookmark_view(bookmark))
        except TraceCorruptError as exc:
            raise InspectionCorruptDataError(str(exc)) from exc
        except TraceUnavailableError as exc:
            raise InspectionUnavailableError(str(exc)) from exc

    def remove_bookmark(self, request: BookmarkRemoveRequest) -> BookmarkRemoveResult:
        if request.sequence is None:
            raise ValueError("bookmark remove requer --sequence")
        try:
            run_id = self._selected_bookmark_run(request.run_id)
            removed = self._bookmarks.remove(run_id, request.sequence)
            return BookmarkRemoveResult(removed, run_id, request.sequence)
        except TraceCorruptError as exc:
            raise InspectionCorruptDataError(str(exc)) from exc
        except TraceUnavailableError as exc:
            raise InspectionUnavailableError(str(exc)) from exc

    def export_diagnostics(self, request: DiagnosticExportRequest) -> DiagnosticExportResult:
        try:
            receipt = self._exporter.export(
                request.run_id,
                output=request.output,
                force=request.force,
                include_bookmarks=request.include_bookmarks,
            )
            return DiagnosticExportResult(
                path=receipt.path,
                run_id=receipt.run_id,
                completeness=receipt.completeness,
                sha256=receipt.sha256,
                size_bytes=receipt.size_bytes,
                files=receipt.files,
                overwritten=receipt.overwritten,
            )
        except TraceCorruptError as exc:
            raise InspectionCorruptDataError(str(exc)) from exc
        except TraceUnavailableError as exc:
            raise InspectionUnavailableError(str(exc)) from exc

    def _selected_bookmark_run(self, run_id: str | None) -> str:
        return run_id or self._inspection.select().metadata.run_id

    @staticmethod
    def _bookmark_view(bookmark: Bookmark) -> BookmarkView:
        projection = bookmark.to_dict()
        schema_version = projection.get("schema_version")
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise ValueError("Agent bookmark projection has an invalid schema version")
        return BookmarkView(
            schema_version=schema_version,
            run_id=bookmark.run_id,
            sequence=bookmark.sequence,
            note=bookmark.note,
            created_at=bookmark.created_at,
        )
