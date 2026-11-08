from __future__ import annotations

from concurrent.futures import Future, TimeoutError
from threading import Event
from typing import Any

import pytest

from llm_agent.application.services.queries import (
    WorkspaceQueryKind,
    WorkspaceQueryRequest,
    WorkspaceQueryResult,
    WorkspaceQueryStatus,
)
from llm_agent.application.task_execution import TaskExecutionStart
from llm_agent.interactive.session import InteractiveSession
from llm_agent.interfaces.workbench import _bridge_queries
from llm_agent.interfaces.workbench._bridge_queries import result_with_recovery
from llm_agent.interfaces.workbench.webview import WorkbenchBridge
from llm_agent.workspace.paths import AppPaths


class _TimeoutOnceFuture(Future[Any]):
    def __init__(self) -> None:
        super().__init__()
        self.drained = Event()

    def result(self, timeout: float | None = None) -> Any:
        if timeout is not None:
            raise TimeoutError("simulated API wait timeout")
        self.drained.set()
        return {"status": "succeeded", "answer": "done"}, None


class _QuerySession:
    def __init__(self) -> None:
        self.future = _TimeoutOnceFuture()

    def query(self, _request: Any) -> Future[Any]:
        return self.future


def test_bridge_timeout_keeps_collecting_settlement_in_background() -> None:
    session = _QuerySession()
    bridge = WorkbenchBridge(session=session)

    response = bridge.query_workspace("list_files", {})

    assert response["accepted"] is False
    assert "simulated API wait timeout" in response["message"]
    assert session.future.drained.wait(2)
    bridge.close()


def test_real_settlement_timeout_releases_query_owner_after_worker_finishes(tmp_path) -> None:
    session = InteractiveSession(
        TaskExecutionStart(
            tmp_path,
            AppPaths.discover(tmp_path / "home", env={}),
            configure_logging=False,
        )
    )
    request = WorkspaceQueryRequest(WorkspaceQueryKind.LIST_FILES, {"path": "."})
    started = Event()
    release = Event()

    def slow_query(_request: Any, _cancellation: Any) -> WorkspaceQueryResult:
        started.set()
        assert release.wait(2)
        return WorkspaceQueryResult(
            WorkspaceQueryKind.LIST_FILES,
            WorkspaceQueryStatus.SUCCEEDED,
            data={"items": []},
        )

    session.query_service.execute = slow_query
    future = session.query(request)
    settled = Event()
    future.add_done_callback(lambda _future: settled.set())
    try:
        assert started.wait(2)
        with pytest.raises(TimeoutError):
            result_with_recovery(future, timeout=0.01)
        assert session.queries.is_busy()

        release.set()
        assert settled.wait(2)
        assert future.done()
        assert not session.queries.is_busy()

        session.query_service.execute = lambda query, _cancel: WorkspaceQueryResult(
            query.kind,
            WorkspaceQueryStatus.SUCCEEDED,
            data={"items": []},
        )
        next_observation, _reference = session.query(request).result(2)
        assert next_observation["status"] == "succeeded"
    finally:
        release.set()
        session.close().result(5)


def test_waiter_start_failure_uses_only_a_bounded_recovery_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    future = _TimeoutOnceFuture()
    waits: list[float | None] = []
    original_result = future.result

    def record_timeout(timeout: float | None = None) -> Any:
        waits.append(timeout)
        return original_result(timeout)

    def fail_start(_thread: Any) -> None:
        raise RuntimeError("waiter thread unavailable")

    monkeypatch.setattr(future, "result", record_timeout)
    monkeypatch.setattr(_bridge_queries.threading.Thread, "start", fail_start)

    with pytest.raises(TimeoutError):
        result_with_recovery(future, timeout=0.01)

    assert waits == [0.01, _bridge_queries.RECOVERY_WAIT_SECONDS]
