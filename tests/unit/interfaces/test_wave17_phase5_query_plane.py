from __future__ import annotations

import time
from io import BytesIO
from pathlib import Path
from queue import Queue
from threading import Event, Thread

import pytest

from agent.interfaces.cli.query_plane import (
    MAX_LIST_ITEMS,
    BoundedQueryExecutor,
    QueryRequest,
    QueryResult,
    ReadOnlyWorkspaceQueryService,
)
from agent.runtime.workspace_context import WorkspaceContext


def _request(workspace: WorkspaceContext, command_id: str, arguments: dict, *, active: bool = False) -> QueryRequest:
    return QueryRequest(1, workspace.workspace_id, 1, command_id, arguments, active)


def test_queries_are_confined_bounded_and_do_not_use_scratch(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("marker\n" * 5, encoding="utf-8")
    (tmp_path / "b.md").write_text("other\n", encoding="utf-8")
    workspace = WorkspaceContext.create(tmp_path)
    service = ReadOnlyWorkspaceQueryService(workspace)
    before = sorted(path.name for path in tmp_path.iterdir())

    listing = service.execute(_request(workspace, "list_files", {"path": "."}, active=True), Event())
    read = service.execute(_request(workspace, "read", {"file_path": "a.py"}, active=True), Event())
    found = service.execute(_request(workspace, "find", {"pattern": "marker", "path": "."}, active=True), Event())

    assert listing.ok and read.ok and found.ok
    assert listing.live_marker is not None and "LIVE SNAPSHOT" in listing.live_marker
    assert read.data["content"].replace("\r\n", "\n") == "marker\n" * 5
    assert found.data["matches"][0]["file"] == "a.py"
    assert sorted(path.name for path in tmp_path.iterdir()) == before
    escaped = service.execute(_request(workspace, "read", {"file_path": "../outside.txt"}), Event())
    assert not escaped.ok


def test_query_executor_has_one_active_operation_and_settles_cancellation(tmp_path: Path) -> None:
    workspace = WorkspaceContext.create(tmp_path)
    executor = BoundedQueryExecutor(workspace_id=workspace.workspace_id)
    release = Event()
    started = Event()

    def slow(request, cancel):
        started.set()
        release.wait(5)
        return type("Result", (), {
            "query_generation": request.query_generation,
            "workspace_id": request.workspace_id,
            "workspace_generation": request.workspace_generation,
            "command_id": request.command_id,
            "ok": True,
            "data": "done",
        })()

    first = executor.submit("read", {}, task_active=True, execute=slow)
    assert isinstance(first, QueryRequest)
    assert started.wait(2)
    second = executor.submit("find", {}, task_active=True, execute=slow)
    assert getattr(second, "error", None) == "QUERY_BUSY"
    release.set()
    assert executor.cancel_and_wait() is not None
    assert executor.is_busy() is False


def test_git_contract_is_fixed_noninteractive_and_disables_helpers(tmp_path: Path, monkeypatch) -> None:
    workspace = WorkspaceContext.create(tmp_path)
    service = ReadOnlyWorkspaceQueryService(workspace)
    captured: dict[str, object] = {}

    class _Process:
        stdout = BytesIO(b" M tracked.py\n")
        stderr = BytesIO(b"")

        def poll(self):
            return 0

        def wait(self, **_kwargs):
            return 0

        def kill(self):
            captured["killed"] = True

    monkeypatch.setattr("agent.interfaces.cli.query_plane.shutil.which", lambda _name: "git-safe")
    monkeypatch.setattr("agent.interfaces.cli.query_plane.subprocess.Popen", lambda *args, **kwargs: captured.update(kwargs) or _Process())
    result = service.git_status(_request(workspace, "git_status", {}), Event())
    assert result.ok
    command = captured["args"] if "args" in captured else None
    # Popen was called positionally with argv; inspect via the call recorder.
    # The contract-specific kwargs are the security assertion here.
    assert captured["shell"] is False
    assert captured["stdin"] is __import__("subprocess").DEVNULL
    assert captured["cwd"] == str(tmp_path.resolve())
    environment = captured["env"]
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_OPTIONAL_LOCKS"] == "0"
    assert environment["GIT_EXTERNAL_DIFF"] == ""
    del command


def test_diff_rejects_option_injection_and_giant_file_is_explicitly_truncated(tmp_path: Path) -> None:
    giant = tmp_path / "giant.txt"
    giant.write_text("x" * 200_000, encoding="utf-8")
    workspace = WorkspaceContext.create(tmp_path)
    service = ReadOnlyWorkspaceQueryService(workspace)
    rejected = service.diff(_request(workspace, "diff", {"paths": ("--output=evil",)}), Event())
    assert not rejected.ok
    read = service.read_file(_request(workspace, "read", {"file_path": "giant.txt"}), Event())
    assert read.ok and read.truncated


def test_ls_is_sorted_and_bounded_for_a_large_directory(tmp_path: Path) -> None:
    for index in range(MAX_LIST_ITEMS + 75):
        (tmp_path / f"file-{index:04d}.txt").write_text("x", encoding="utf-8")
    workspace = WorkspaceContext.create(tmp_path)
    result = ReadOnlyWorkspaceQueryService(workspace).list_files(_request(workspace, "list_files", {"path": "."}), Event())

    assert result.ok
    names = [row["name"] for row in result.data["items"]]
    assert len(names) == MAX_LIST_ITEMS
    assert names == sorted(names, key=lambda value: (value.casefold(), value))
    assert result.truncated is True


def test_find_and_read_reject_symlink_escape_and_pathological_regex(tmp_path: Path) -> None:
    outside = tmp_path.parent / "wave17-outside.txt"
    outside.write_text("secret-outside", encoding="utf-8")
    link = tmp_path / "escape.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this host")
    (tmp_path / "regex.txt").write_text("a" * 20_000 + "b", encoding="utf-8")
    workspace = WorkspaceContext.create(tmp_path)
    service = ReadOnlyWorkspaceQueryService(workspace)

    assert not service.read_file(_request(workspace, "read", {"file_path": "escape.txt"}), Event()).ok
    found_escape = service.find(_request(workspace, "find", {"pattern": "secret-outside", "path": "."}), Event())
    assert found_escape.ok and found_escape.data["matches"] == []
    started = time.monotonic()
    pathological = service.find(_request(workspace, "find", {"pattern": "(a+)+$", "path": "."}), Event())
    assert not pathological.ok
    assert time.monotonic() - started < 1.0


def test_find_uses_literal_bounded_matching_and_observes_cancel_inside_long_line(tmp_path: Path) -> None:
    long_line = tmp_path / "long-line.txt"
    long_line.write_text("a" * 128_000 + "\n", encoding="utf-8")
    workspace = WorkspaceContext.create(tmp_path)
    service = ReadOnlyWorkspaceQueryService(workspace)

    for pattern in (
        "a*a*a*a*a*a*a*b",
        "a{1,3}a{1,3}a{1,3}a{1,3}b",
        "(a+)+$",
    ):
        started = time.monotonic()
        rejected = service.find(_request(workspace, "find", {"pattern": pattern, "path": "."}), Event())
        assert not rejected.ok
        assert time.monotonic() - started < 1.0

    started = time.monotonic()
    literal = service.find(_request(workspace, "find", {"pattern": "needle", "path": "long-line.txt"}), Event())
    assert literal.ok and literal.data["matches"] == []
    assert time.monotonic() - started < 2.0

    class _CancelDuringMatch:
        checks = 0

        def is_set(self) -> bool:
            self.checks += 1
            return self.checks >= 3

    cancel = _CancelDuringMatch()
    cancelled = service.find(
        _request(workspace, "find", {"pattern": "needle", "path": "long-line.txt"}),
        cancel,
    )
    assert not cancelled.ok and "cancelado" in (cancelled.error or "")
    assert cancel.checks >= 3


def test_query_result_publication_and_poll_are_linearized_without_busy_wedge(tmp_path: Path) -> None:
    workspace = WorkspaceContext.create(tmp_path)
    executor = BoundedQueryExecutor(workspace_id=workspace.workspace_id)

    def quick(request, _cancel):
        return QueryResult(request.query_generation, request.workspace_id, request.workspace_generation, request.command_id, True, data="ok")

    for _ in range(50):
        request = executor.submit("read", {}, task_active=False, execute=quick)
        assert isinstance(request, QueryRequest)
        deadline = time.monotonic() + 2
        result = None
        while result is None and time.monotonic() < deadline:
            result = executor.poll()
        assert result is not None and result.ok
        assert executor.is_busy() is False


def test_forced_poll_race_cannot_consume_before_pending_publication(tmp_path: Path) -> None:
    workspace = WorkspaceContext.create(tmp_path)
    executor = BoundedQueryExecutor(workspace_id=workspace.workspace_id)
    poll_results: list[object] = []
    poll_threads: list[Thread] = []
    published = Event()

    class _ProbeQueue(Queue):
        def put_nowait(self, item):
            super().put_nowait(item)
            thread = Thread(target=lambda: poll_results.append(executor.poll()))
            poll_threads.append(thread)
            thread.start()
            published.set()

    executor._channel = _ProbeQueue(maxsize=1)

    def quick(request, _cancel):
        return QueryResult(request.query_generation, request.workspace_id, request.workspace_generation, request.command_id, True, data="ok")

    assert isinstance(executor.submit("read", {}, task_active=False, execute=quick), QueryRequest)
    assert published.wait(2)
    poll_threads[0].join(timeout=2)
    assert not poll_threads[0].is_alive()
    assert poll_results and getattr(poll_results[0], "data", None) == "ok"
    assert executor.is_busy() is False


def test_query_shutdown_reports_timeout_without_abandoning_the_worker(tmp_path: Path) -> None:
    workspace = WorkspaceContext.create(tmp_path)
    executor = BoundedQueryExecutor(workspace_id=workspace.workspace_id)
    started = Event()
    release = Event()

    def slow(request, _cancel):
        started.set()
        release.wait(5)
        return QueryResult(request.query_generation, request.workspace_id, request.workspace_generation, request.command_id, True, data="done")

    assert isinstance(executor.submit("read", {}, task_active=False, execute=slow), QueryRequest)
    assert started.wait(2)
    timeout = executor.cancel_and_wait(timeout_seconds=0.01)
    assert timeout is not None and timeout.error == "QUERY_SHUTDOWN_TIMEOUT"
    assert executor.is_busy() is True
    release.set()
    settled = executor.cancel_and_wait(timeout_seconds=2)
    assert settled is not None and settled.data == "done"
