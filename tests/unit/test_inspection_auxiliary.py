from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from llm_agent.agent.observability import TraceStore
from llm_agent.agent.observability.bookmarks import Bookmark
from llm_agent.agent.observability.export import ExportReceipt
from llm_agent.agent.observability.trace_store import TraceCorruptError, TraceUnavailableError
from llm_agent.agent.runtime.correlation import RunCorrelation
from llm_agent.agent.runtime.event_kinds import RuntimeEventKind
from llm_agent.agent.runtime.events import RuntimeEvent
from llm_agent.application.inspection import (
    BookmarkAddRequest,
    BookmarkListRequest,
    BookmarkRemoveRequest,
    DiagnosticExportRequest,
    InspectionAuxiliaryOperations,
    InspectionCorruptDataError,
    InspectionUnavailableError,
)
from llm_agent.workspace.paths import WorkspacePaths


def _inspection_workspace(tmp_path: Path) -> tuple[WorkspacePaths, RunCorrelation]:
    paths = WorkspacePaths(
        "inspection-auxiliary",
        tmp_path / "data",
        tmp_path / "state",
        tmp_path / "cache",
    )
    paths.ensure_directories()
    correlation = RunCorrelation.fresh()
    trace = TraceStore(
        paths,
        correlation.run_id,
        root_task_id=correlation.root_task_id,
        mode="trace",
    )
    trace.append(
        RuntimeEvent.from_fields(
            RuntimeEventKind.WARNING,
            correlation,
            {"summary": "safe warning", "api_key": "TOP-SECRET"},
        )
    )
    trace.set_final_outcome({"status": "succeeded"})
    trace.close()
    return paths, correlation


def test_application_bookmark_operations_keep_agent_objects_internal(tmp_path: Path) -> None:
    paths, correlation = _inspection_workspace(tmp_path)
    operations = InspectionAuxiliaryOperations(paths)

    added = operations.add_bookmark(BookmarkAddRequest(sequence=1, note="bounded note"))
    listed = operations.list_bookmarks(BookmarkListRequest())
    removed = operations.remove_bookmark(BookmarkRemoveRequest(sequence=1))

    assert added.bookmark.run_id == correlation.run_id
    assert added.bookmark.to_dict() == {
        "schema_version": 1,
        "run_id": correlation.run_id,
        "sequence": 1,
        "note": "bounded note",
        "created_at": added.bookmark.created_at,
    }
    assert listed.to_dict()["bookmarks"] == [added.bookmark.to_dict()]
    assert removed.to_dict() == {
        "removed": True,
        "run_id": correlation.run_id,
        "sequence": 1,
    }
    assert not isinstance(added.bookmark, Bookmark)


def test_application_bookmark_operations_translate_agent_trace_errors(tmp_path: Path) -> None:
    paths, correlation = _inspection_workspace(tmp_path)
    operations = InspectionAuxiliaryOperations(paths)

    with pytest.raises(InspectionUnavailableError) as unavailable:
        operations.add_bookmark(BookmarkAddRequest(run_id=correlation.run_id, sequence=999))
    assert str(unavailable.value) == "bookmark sequence is not present in the selected trace"
    assert isinstance(unavailable.value.__cause__, TraceUnavailableError)

    trace = TraceStore.open(paths, correlation.run_id)
    (trace.run_dir / "bookmarks.json").write_text("{invalid", encoding="utf-8")
    with pytest.raises(InspectionCorruptDataError) as corrupt:
        operations.list_bookmarks(BookmarkListRequest(run_id=correlation.run_id))
    assert isinstance(corrupt.value.__cause__, TraceCorruptError)


def test_application_diagnostic_export_projects_receipt_and_preserves_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import llm_agent.application.inspection.operations as operations_module

    paths, correlation = _inspection_workspace(tmp_path)
    observed: dict[str, object] = {}

    class RecordingExporter:
        def __init__(self, service: object) -> None:
            observed["service"] = service

        def export(
            self,
            run_id: str | None,
            *,
            output: str | Path | None,
            force: bool,
            include_bookmarks: bool,
        ) -> ExportReceipt:
            observed.update(
                run_id=run_id,
                output=output,
                force=force,
                include_bookmarks=include_bookmarks,
            )
            return ExportReceipt(
                path=str(tmp_path / "bundle.zip"),
                run_id=correlation.run_id,
                completeness="complete",
                sha256="a" * 64,
                size_bytes=321,
                files=("manifest.json", "metadata.json"),
                overwritten=True,
            )

    monkeypatch.setattr(operations_module, "DiagnosticExporter", RecordingExporter)
    operations = InspectionAuxiliaryOperations(paths)
    request = DiagnosticExportRequest(
        run_id=correlation.run_id,
        output=tmp_path / "bundle.zip",
        force=True,
        include_bookmarks=True,
    )

    result = operations.export_diagnostics(request)

    assert observed["run_id"] == correlation.run_id
    assert observed["output"] == tmp_path / "bundle.zip"
    assert observed["force"] is True
    assert observed["include_bookmarks"] is True
    assert not isinstance(result, ExportReceipt)
    assert result.to_dict() == {
        "path": str(tmp_path / "bundle.zip"),
        "run_id": correlation.run_id,
        "completeness": "complete",
        "sha256": "a" * 64,
        "size_bytes": 321,
        "files": ["manifest.json", "metadata.json"],
        "overwritten": True,
    }


def test_application_diagnostic_export_keeps_agent_archive_safety(tmp_path: Path) -> None:
    paths, correlation = _inspection_workspace(tmp_path)
    operations = InspectionAuxiliaryOperations(paths)
    operations.add_bookmark(
        BookmarkAddRequest(run_id=correlation.run_id, sequence=1, note="safe note")
    )
    destination = tmp_path / "diagnostic.zip"

    result = operations.export_diagnostics(
        DiagnosticExportRequest(
            run_id=correlation.run_id,
            output=destination,
            include_bookmarks=True,
        )
    )

    assert result.path == str(destination.resolve())
    assert "bookmarks.json" in result.files
    assert result.sha256 == hashlib.sha256(destination.read_bytes()).hexdigest()
    with zipfile.ZipFile(destination) as archive:
        assert "bookmarks.json" in archive.namelist()
        assert b"TOP-SECRET" not in b"".join(archive.read(name) for name in archive.namelist())
    with pytest.raises(FileExistsError):
        operations.export_diagnostics(
            DiagnosticExportRequest(run_id=correlation.run_id, output=destination)
        )
    overwritten = operations.export_diagnostics(
        DiagnosticExportRequest(
            run_id=correlation.run_id,
            output=destination,
            force=True,
            include_bookmarks=True,
        )
    )
    assert overwritten.overwritten is True
    assert overwritten.sha256 == result.sha256
