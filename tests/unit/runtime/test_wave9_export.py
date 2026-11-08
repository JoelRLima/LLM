from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from llm_agent.agent.observability import TraceStore
from llm_agent.agent.observability.bookmarks import BookmarkStore
from llm_agent.agent.observability.export import DiagnosticExporter
from llm_agent.agent.presentation import InspectionService
from llm_agent.agent.runtime.correlation import RunCorrelation
from llm_agent.agent.runtime.event_kinds import RuntimeEventKind
from llm_agent.agent.runtime.events import RuntimeEvent
from llm_agent.workspace.paths import WorkspacePaths


def _fixture(tmp_path: Path) -> tuple[WorkspacePaths, RunCorrelation]:
    paths = WorkspacePaths("export", tmp_path / "data", tmp_path / "state", tmp_path / "cache")
    paths.ensure_directories()
    correlation = RunCorrelation.fresh()
    store = TraceStore(paths, correlation.run_id, root_task_id=correlation.root_task_id, mode="trace")
    store.append(
        RuntimeEvent.from_fields(
            RuntimeEventKind.WARNING,
            correlation,
            {"api_key": "TOP-SECRET", "summary": "safe warning", "prompt": "hidden"},
        )
    )
    store.set_final_outcome({"status": "succeeded", "password": "TOP-SECRET"})
    store.close()
    BookmarkStore(paths).add(correlation.run_id, 1, "safe note")
    return paths, correlation


def test_export_manifest_hashes_collision_and_redaction(tmp_path: Path) -> None:
    paths, correlation = _fixture(tmp_path)
    service = InspectionService(paths)
    destination = tmp_path / "bundle.zip"
    exporter = DiagnosticExporter(service)
    receipt = exporter.export(correlation.run_id, output=destination, include_bookmarks=True)
    assert receipt.path == str(destination.absolute())
    assert receipt.completeness == "complete"
    with zipfile.ZipFile(destination) as archive:
        names = tuple(sorted(archive.namelist()))
        manifest = json.loads(archive.read("manifest.json"))
        assert "manifest.json" in names
        assert manifest["trace_completeness"] == "complete"
        for name, digest in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
        payload = b"".join(archive.read(name) for name in names)
        assert b"TOP-SECRET" not in payload
        assert b"hidden" not in archive.read("trace.jsonl")
        assert b"checkpoint" not in payload

    with pytest.raises(FileExistsError):
        exporter.export(correlation.run_id, output=destination)
    second = exporter.export(correlation.run_id, output=destination, force=True, include_bookmarks=True)
    assert second.sha256 == receipt.sha256


def test_export_default_is_separate_from_retention(tmp_path: Path) -> None:
    paths, correlation = _fixture(tmp_path)
    receipt = DiagnosticExporter(InspectionService(paths)).export(correlation.run_id)
    assert Path(receipt.path).parent == paths.trace_exports_dir
    assert Path(receipt.path).exists()


def test_metadata_export_omits_all_payload_and_notes_without_building_content_snapshot(tmp_path: Path, monkeypatch) -> None:
    paths, correlation = _fixture(tmp_path)
    service = InspectionService(paths)
    monkeypatch.setattr(service, "snapshot", lambda *a, **k: pytest.fail("metadata export must not build content snapshot"))
    exporter = DiagnosticExporter(service)
    receipt = exporter.export(correlation.run_id, metadata_only=True)
    with zipfile.ZipFile(receipt.path) as archive:
        assert "trace.jsonl" not in archive.namelist()
        assert "bookmarks.json" not in archive.namelist()
        payload = b"".join(archive.read(name) for name in archive.namelist())
        assert b"safe warning" not in payload
        assert b"safe note" not in payload
        for line in archive.read("events-metadata.jsonl").decode().splitlines():
            assert "payload" not in json.loads(line)
        assert json.loads(archive.read("manifest.json"))["privacy_mode"] == "metadata_only"
    with pytest.raises(ValueError, match="bookmark"):
        exporter.export(correlation.run_id, metadata_only=True, include_bookmarks=True)
