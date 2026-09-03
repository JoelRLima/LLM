from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent.observability import TraceCorruptError, TraceStore, TraceStoreError, safe_run_key
from agent.observability.bookmarks import BookmarkStore
from agent.observability.export import DiagnosticExporter
from agent.presentation import InspectionService
from agent.runtime.path_safety import WorkspacePathError, assert_owned_path
from agent.runtime.paths import WorkspacePaths


def _paths(tmp_path: Path, *, state: Path | None = None) -> WorkspacePaths:
    paths = WorkspacePaths("wave9-paths", tmp_path / "data", state or tmp_path / "state", tmp_path / "cache")
    paths.ensure_directories()
    return paths


def _link_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if os.name == "nt":
            pytest.skip(f"symlink unavailable: {exc}")
        raise


def test_owned_path_rejects_parent_traversal_before_publication(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("unchanged", encoding="utf-8")

    with pytest.raises(WorkspacePathError):
        assert_owned_path(paths.traces_dir, outside)

    assert outside.read_text(encoding="utf-8") == "unchanged"


def test_trace_root_rejects_redirected_ancestor_without_touching_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    linked_state = tmp_path / "linked-state"
    _link_or_skip(linked_state, outside, directory=True)

    paths = WorkspacePaths("wave9-paths", tmp_path / "data", linked_state, tmp_path / "cache")
    with pytest.raises(TraceStoreError):
        TraceStore(paths, "redirected-run")

    assert sentinel.read_text(encoding="utf-8") == "unchanged"
    assert not (outside / "traces").exists()


def test_run_directory_symlink_is_rejected_before_trace_publication(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    outside = tmp_path / "outside-run"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    run_id = "redirected-run"
    linked_run = paths.traces_dir / safe_run_key(run_id)
    _link_or_skip(linked_run, outside, directory=True)

    with pytest.raises(TraceStoreError):
        TraceStore(paths, run_id)

    assert sentinel.read_text(encoding="utf-8") == "unchanged"


def test_bookmark_and_export_redirection_are_rejected_without_read_or_write(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    run_id = "safe-run"
    store = TraceStore(paths, run_id, shutdown_timeout_seconds=2)
    store.close()
    outside = tmp_path / "outside-artifacts"
    outside.mkdir()
    sentinel = outside / "payload.json"
    sentinel.write_text(json.dumps({"untouched": True}), encoding="utf-8")

    _link_or_skip(store.run_dir / "bookmarks.json", sentinel)
    with pytest.raises(TraceCorruptError):
        BookmarkStore(paths).list(run_id)

    output_dir = tmp_path / "output-link"
    _link_or_skip(output_dir, outside, directory=True)
    with pytest.raises(FileExistsError):
        DiagnosticExporter(InspectionService(paths))._safe_output(output_dir / "bundle.zip")
    assert json.loads(sentinel.read_text(encoding="utf-8")) == {"untouched": True}
