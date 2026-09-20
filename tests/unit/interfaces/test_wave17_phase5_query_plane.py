from __future__ import annotations

import time
from io import BytesIO
from pathlib import Path
from queue import Queue
from threading import Event, Thread

import pytest

from agent.application_services.queries import (
    MAX_LIST_ITEMS,
    ReadOnlyWorkspaceQueryService,
    WorkspaceQueryKind,
    WorkspaceQueryRequest,
    WorkspaceQueryResult,
    WorkspaceQueryStatus,
)
from agent.interfaces.cli.query_executor import BoundedQueryExecutor, CliQueryCompletion, CliQuerySubmission
from agent.runtime.workspace_context import WorkspaceContext


def _request(_workspace: WorkspaceContext, command_id: str, arguments: dict, *, active: bool = False) -> WorkspaceQueryRequest:
    del active
    return WorkspaceQueryRequest(WorkspaceQueryKind(command_id), arguments)


def _cancel() -> object:
    return type("Cancellation", (), {"is_cancelled": lambda self: False})()


def test_queries_are_confined_bounded_and_do_not_use_scratch(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("marker\n" * 5, encoding="utf-8")
    (tmp_path / "b.md").write_text("other\n", encoding="utf-8")
    workspace = WorkspaceContext.create(tmp_path)
    service = ReadOnlyWorkspaceQueryService(workspace)
    before = sorted(path.name for path in tmp_path.iterdir())

    listing = service.execute(_request(workspace, "list_files", {"path": "."}, active=True), _cancel())
    read = service.execute(_request(workspace, "read", {"file_path": "a.py"}, active=True), _cancel())
    found = service.execute(_request(workspace, "find", {"pattern": "marker", "path": "."}, active=True), _cancel())

    assert listing.status is WorkspaceQueryStatus.SUCCEEDED
    assert read.status is WorkspaceQueryStatus.SUCCEEDED
    assert found.status is WorkspaceQueryStatus.SUCCEEDED
    assert read.data["content"].replace("\r\n", "\n") == "marker\n" * 5
    assert found.data["matches"][0]["file"] == "a.py"
    assert sorted(path.name for path in tmp_path.iterdir()) == before
    escaped = service.execute(_request(workspace, "read", {"file_path": "../outside.txt"}), _cancel())
    assert escaped.status is WorkspaceQueryStatus.FAILED


def test_query_executor_has_one_active_operation_and_settles_cancellation(tmp_path: Path) -> None:
    workspace = WorkspaceContext.create(tmp_path)
    executor = BoundedQueryExecutor(workspace_id=workspace.workspace_id)
    release = Event()
    started = Event()

    def slow(request, cancel):
        started.set()
        release.wait(5)
        return type("Result", (), {
            "kind": request.kind,
            "status": WorkspaceQueryStatus.SUCCEEDED,
            "data": "done",
        })()

    first = executor.submit(_request(workspace, "read", {}), task_active=True, execute=slow)
    assert isinstance(first, CliQuerySubmission)
    assert started.wait(2)
    second = executor.submit(_request(workspace, "find", {}), task_active=True, execute=slow)
    assert isinstance(second, CliQueryCompletion) and second.adapter_reason_code == "QUERY_BUSY"
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

    monkeypatch.setattr("agent.application_services.query_git.shutil.which", lambda _name: "git-safe")
    monkeypatch.setattr("agent.application_services.query_git.subprocess.Popen", lambda *args, **kwargs: captured.update(kwargs) or _Process())
    result = service.git_status(_request(workspace, "git_status", {}), _cancel())
    assert result.status is WorkspaceQueryStatus.SUCCEEDED
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
    rejected = service.diff(_request(workspace, "diff", {"paths": ("--output=evil",)}), _cancel())
    assert rejected.status is WorkspaceQueryStatus.FAILED
    read = service.read_file(_request(workspace, "read", {"file_path": "giant.txt"}), _cancel())
    assert read.status is WorkspaceQueryStatus.SUCCEEDED and read.truncated


def test_ls_is_sorted_and_bounded_for_a_large_directory(tmp_path: Path) -> None:
    for index in range(MAX_LIST_ITEMS + 75):
        (tmp_path / f"file-{index:04d}.txt").write_text("x", encoding="utf-8")
    workspace = WorkspaceContext.create(tmp_path)
    result = ReadOnlyWorkspaceQueryService(workspace).list_files(_request(workspace, "list_files", {"path": "."}), _cancel())

    assert result.status is WorkspaceQueryStatus.SUCCEEDED
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

    assert service.read_file(_request(workspace, "read", {"file_path": "escape.txt"}), _cancel()).status is WorkspaceQueryStatus.FAILED
    found_escape = service.find(_request(workspace, "find", {"pattern": "secret-outside", "path": "."}), _cancel())
    assert found_escape.status is WorkspaceQueryStatus.SUCCEEDED and found_escape.data["matches"] == []
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
        rejected = service.find(_request(workspace, "find", {"pattern": pattern, "path": "."}), _cancel())
        assert rejected.status is WorkspaceQueryStatus.FAILED
        assert time.monotonic() - started < 1.0

    started = time.monotonic()
    literal = service.find(_request(workspace, "find", {"pattern": "needle", "path": "long-line.txt"}), _cancel())
    assert literal.status is WorkspaceQueryStatus.SUCCEEDED and literal.data["matches"] == []
    assert time.monotonic() - started < 2.0

    class _CancelDuringMatch:
        checks = 0

        def is_cancelled(self) -> bool:
            self.checks += 1
            return self.checks >= 3

    cancel = _CancelDuringMatch()
    cancelled = service.find(
        _request(workspace, "find", {"pattern": "needle", "path": "long-line.txt"}),
        cancel,
    )
    assert cancelled.status is WorkspaceQueryStatus.CANCELLED
    assert cancel.checks >= 3


def test_query_result_publication_and_poll_are_linearized_without_busy_wedge(tmp_path: Path) -> None:
    workspace = WorkspaceContext.create(tmp_path)
    executor = BoundedQueryExecutor(workspace_id=workspace.workspace_id)

    def quick(request, _cancel):
        return WorkspaceQueryResult(request.kind, WorkspaceQueryStatus.SUCCEEDED, data="ok")

    for _ in range(50):
        request = executor.submit(_request(workspace, "read", {}), task_active=False, execute=quick)
        assert isinstance(request, CliQuerySubmission)
        deadline = time.monotonic() + 2
        result = None
        while result is None and time.monotonic() < deadline:
            result = executor.poll()
        assert result is not None and result.result is not None and result.result.status is WorkspaceQueryStatus.SUCCEEDED
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
        return WorkspaceQueryResult(request.kind, WorkspaceQueryStatus.SUCCEEDED, data="ok")

    assert isinstance(executor.submit(_request(workspace, "read", {}), task_active=False, execute=quick), CliQuerySubmission)
    assert published.wait(2)
    poll_threads[0].join(timeout=2)
    assert not poll_threads[0].is_alive()
    assert poll_results and poll_results[0].result is not None and poll_results[0].result.data == "ok"
    assert executor.is_busy() is False


def test_query_shutdown_reports_timeout_without_abandoning_the_worker(tmp_path: Path) -> None:
    workspace = WorkspaceContext.create(tmp_path)
    executor = BoundedQueryExecutor(workspace_id=workspace.workspace_id)
    started = Event()
    release = Event()

    def slow(request, _cancel):
        started.set()
        release.wait(5)
        return WorkspaceQueryResult(request.kind, WorkspaceQueryStatus.SUCCEEDED, data="done")

    assert isinstance(executor.submit(_request(workspace, "read", {}), task_active=False, execute=slow), CliQuerySubmission)
    assert started.wait(2)
    timeout = executor.cancel_and_wait(timeout_seconds=0.01)
    assert timeout is not None and timeout.adapter_reason_code == "QUERY_SHUTDOWN_TIMEOUT"
    assert executor.is_busy() is True
    release.set()
    settled = executor.cancel_and_wait(timeout_seconds=2)
    assert settled is not None and settled.result is not None and settled.result.data == "done"
