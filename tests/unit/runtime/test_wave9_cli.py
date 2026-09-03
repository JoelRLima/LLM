from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.interfaces.cli import inspector as inspector_module
from agent.interfaces.cli.app import main
from agent.observability import TraceStore
from agent.observability.bookmarks import BookmarkStore
from agent.presentation import InspectionService
from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_kinds import RuntimeEventKind
from agent.runtime.events import RuntimeEvent
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext


def _fixture(tmp_path: Path) -> tuple[Path, Path, RunCorrelation]:
    app_home = tmp_path / "app-home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    app_paths = AppPaths.discover(app_home=app_home)
    workspace_paths = app_paths.for_workspace(WorkspaceContext.create(workspace).workspace_id)
    workspace_paths.ensure_directories()
    correlation = RunCorrelation.fresh()
    store = TraceStore(workspace_paths, correlation.run_id, root_task_id=correlation.root_task_id)
    store.append(RuntimeEvent.from_fields(RuntimeEventKind.TASK_NODE_STARTED, correlation, {"summary": "started"}))
    store.set_final_outcome({"status": "succeeded"})
    store.close()
    return app_home, workspace, correlation


def test_inspect_list_and_show_emit_one_json_document(tmp_path: Path, capsys) -> None:
    app_home, workspace, correlation = _fixture(tmp_path)
    assert main(["inspect", "list", "--json", "--home", str(app_home), "--workspace", str(workspace)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed["runs"]) == 1
    assert listed["runs"][0]["run_id"] == correlation.run_id

    assert main(["inspect", "show", "--json", "--home", str(app_home), "--workspace", str(workspace), "--run-id", correlation.run_id]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["run"]["run_id"] == correlation.run_id
    assert shown["run"]["completeness"] == "complete"


def test_inspect_replay_is_bounded_and_follow_rejects_non_tty(tmp_path: Path, capsys) -> None:
    app_home, workspace, correlation = _fixture(tmp_path)
    assert main(["inspect", "replay", "--json", "--limit", "1", "--home", str(app_home), "--workspace", str(workspace), "--run-id", correlation.run_id]) == 0
    replay = json.loads(capsys.readouterr().out)
    assert len(replay["activities"]) == 1

    assert main(["inspect", "--follow", "--home", str(app_home), "--workspace", str(workspace), "--run-id", correlation.run_id]) == 2
    assert "follow" in capsys.readouterr().err


def test_bookmark_sidecar_does_not_change_trace(tmp_path: Path) -> None:
    app_home, workspace, correlation = _fixture(tmp_path)
    paths = AppPaths.discover(app_home=app_home).for_workspace(WorkspaceContext.create(workspace).workspace_id)
    before = TraceStore.open(paths, correlation.run_id).read_result()
    bookmark = BookmarkStore(paths).add(correlation.run_id, 1, "note")
    after = TraceStore.open(paths, correlation.run_id).read_result()
    assert bookmark.sequence == 1
    assert before.records == after.records
    assert before.completeness == after.completeness


def test_follow_advances_after_bounded_prefix_without_replaying_old_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_home = tmp_path / "app-home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AppPaths.discover(app_home=app_home).for_workspace(WorkspaceContext.create(workspace).workspace_id)
    paths.ensure_directories()
    correlation = RunCorrelation.fresh()
    store = TraceStore(paths, correlation.run_id, root_task_id=correlation.root_task_id, shutdown_timeout_seconds=2)
    store.append(RuntimeEvent.from_fields(RuntimeEventKind.TASK_NODE_STARTED, correlation, timestamp="2026-01-01T00:00:01Z"))
    assert store.flush(2)
    service = InspectionService(paths)
    args = SimpleNamespace(json_output=False, after=0, limit=1, inspect_sequence=None)
    calls: list[int] = []
    rendered: list[tuple[int, ...]] = []
    real_snapshot = service.snapshot

    def snapshot(*positional: object, **keyword: object):
        calls.append(int(keyword["after_sequence"]))
        value = real_snapshot(*positional, **keyword)
        if len(calls) == 1:
            store.append(
                RuntimeEvent.from_fields(
                    RuntimeEventKind.STEP_COMPLETED,
                    correlation,
                    step=2,
                    timestamp="2026-01-01T00:00:02Z",
                )
            )
            assert store.flush(2)
        elif len(calls) == 3:
            raise KeyboardInterrupt
        return value

    monkeypatch.setattr(service, "snapshot", snapshot)
    monkeypatch.setattr(inspector_module, "_is_tty", lambda: True)
    monkeypatch.setattr(inspector_module.console, "clear", lambda: None)
    monkeypatch.setattr(inspector_module, "render_snapshot", lambda value: rendered.append(tuple(item.sequence for item in value.timeline)))

    assert inspector_module._run_follow(service, correlation.run_id, args) == 0
    assert calls == [0, 1, 2]
    assert rendered[:2] == [(1,), (2,)]
    assert [item.sequence for item in TraceStore.open(paths, correlation.run_id).read()] == [1, 2]
    store.close()
