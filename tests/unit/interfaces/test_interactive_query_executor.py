from __future__ import annotations

import threading

import pytest

from llm_agent.application.services.queries import (
    WorkspaceQueryKind,
    WorkspaceQueryRequest,
    WorkspaceQueryResult,
    WorkspaceQueryStatus,
)
from llm_agent.interactive import query_executor


def _request() -> WorkspaceQueryRequest:
    return WorkspaceQueryRequest(WorkspaceQueryKind.LIST_FILES, {"path": "."})


def _success(request: WorkspaceQueryRequest, _cancellation: object) -> WorkspaceQueryResult:
    return WorkspaceQueryResult(request.kind, WorkspaceQueryStatus.SUCCEEDED, data=[])


def test_live_marker_failure_does_not_lose_query_settlement() -> None:
    def fail_marker(_active: bool) -> str:
        raise RuntimeError("presentation marker failed")

    executor = query_executor.BoundedQueryExecutor(
        workspace_id="workspace",
        live_marker_factory=fail_marker,
    )
    executor.submit(_request(), task_active=False, execute=_success)

    completion = executor.wait_for_result(timeout_seconds=2)

    assert completion is not None
    assert completion.result is not None
    assert completion.result.status is WorkspaceQueryStatus.SUCCEEDED
    assert completion.live_marker is None
    assert executor.is_busy() is False


def test_thread_start_failure_before_launch_restores_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = query_executor.BoundedQueryExecutor(workspace_id="workspace")

    def fail_start(_thread: threading.Thread) -> None:
        raise OSError("thread start failed")

    with monkeypatch.context() as patch:
        patch.setattr(query_executor.Thread, "start", fail_start)
        with pytest.raises(OSError, match="thread start failed"):
            executor.submit(_request(), task_active=False, execute=_success)

    assert executor.is_busy() is False
    executor.submit(_request(), task_active=False, execute=_success)
    completion = executor.wait_for_result(timeout_seconds=2)
    assert completion is not None
    assert completion.result is not None
    assert completion.result.status is WorkspaceQueryStatus.SUCCEEDED
    assert executor.is_busy() is False


def test_start_error_after_thread_launch_keeps_query_admitted(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = query_executor.BoundedQueryExecutor(workspace_id="workspace")
    real_start = threading.Thread.start

    def launch_then_raise(worker: threading.Thread) -> None:
        real_start(worker)
        raise OSError("late thread start error")

    with monkeypatch.context() as patch:
        patch.setattr(query_executor.Thread, "start", launch_then_raise)
        submission = executor.submit(_request(), task_active=False, execute=_success)

    assert submission.query_generation == 1
    completion = executor.wait_for_result(timeout_seconds=2)
    assert completion is not None
    assert completion.result is not None
    assert completion.result.status is WorkspaceQueryStatus.SUCCEEDED
    assert executor.is_busy() is False
