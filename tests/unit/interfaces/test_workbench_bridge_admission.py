from __future__ import annotations

import threading
from concurrent.futures import Future

import pytest

from llm_agent.interfaces.workbench import webview
from llm_agent.interfaces.workbench.webview import WorkbenchBridge


class _ConsumingFuture(Future):
    """Owner lifecycle ends only when the bridge consumes the settlement."""

    def __init__(self, owner: "_Session") -> None:
        super().__init__()
        self.owner = owner

    def result(self, timeout=None):
        try:
            return super().result(timeout)
        finally:
            self.owner.busy = False
            self.owner.consumed_count += 1


class _Session:
    def __init__(self, first_error: Exception | None = None, *, hold_settlement=False) -> None:
        self.first_error = first_error
        self.hold_settlement = hold_settlement
        self.submit_count = 0
        self.accepted_count = 0
        self.busy = False
        self.consumed_count = 0
        self.cancel_count = 0
        self.close_count = 0
        self.current_future: _ConsumingFuture | None = None

    def submit(self, _text: str, *, stream_callback=None, _on_admitted=None) -> Future:
        self.submit_count += 1
        if self.first_error is not None:
            error, self.first_error = self.first_error, None
            if stream_callback is not None:
                stream_callback("orphan delta")
            raise error
        if self.busy:
            raise RuntimeError("owner remains busy until settlement is consumed")
        self.busy = True
        self.accepted_count += 1
        future = _ConsumingFuture(self)
        self.current_future = future
        if _on_admitted is not None:
            _on_admitted(future)
        if not self.hold_settlement:
            future.set_result(({"status": "succeeded", "answer": "ok"}, None))
        return future

    def cancel(self) -> None:
        self.cancel_count += 1
        if self.current_future is not None and not self.current_future.done():
            self.current_future.set_result(({"status": "cancelled", "answer": "cancelled"}, None))

    def close(self) -> Future:
        self.close_count += 1
        future: Future = Future()
        future.set_result(None)
        return future


@pytest.mark.parametrize("failure", [OSError("submission I/O failed"), TimeoutError("submission timed out")])
def test_failed_synchronous_submission_restores_bridge_admission(failure: Exception) -> None:
    session = _Session(failure)
    bridge = WorkbenchBridge(session=session)
    try:
        with pytest.raises(type(failure), match=str(failure)):
            bridge.submit("primeira tentativa")

        assert bridge._active_request_id is None
        assert bridge._stream_open is False
        assert bridge._cancel_requested is False
        assert list(bridge._events) == []
        assert session.busy is False

        accepted = bridge.submit("segunda tentativa")
        assert accepted == {"accepted": True, "request_id": "request-2"}
        terminal = bridge.wait_events(timeout_ms=1_000)
        assert terminal == [{
            "type": "settled",
            "request_id": "request-2",
            "observation": {"status": "succeeded", "answer": "ok"},
            "reference": None,
        }]
        assert session.submit_count == 2
        assert session.accepted_count == 1
        assert session.consumed_count == 1
        assert session.busy is False
    finally:
        bridge.close()


@pytest.mark.parametrize(
    ("failure", "raises"),
    [
        pytest.param(OSError("collector start failed"), True, id="os-error"),
        pytest.param(RuntimeError("collector start rejected"), False, id="runtime-rejection"),
    ],
)
def test_collector_start_failure_precedes_owner_admission_and_recovers(monkeypatch, failure, raises) -> None:
    real_thread = threading.Thread
    attempts = 0

    class FailFirstStart(real_thread):
        def start(self) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise failure
            super().start()

    monkeypatch.setattr(webview.threading, "Thread", FailFirstStart)
    session = _Session()
    bridge = WorkbenchBridge(session=session)
    try:
        if raises:
            with pytest.raises(type(failure), match=str(failure)):
                bridge.submit("primeira tentativa")
        else:
            assert bridge.submit("primeira tentativa") == {
                "accepted": False,
                "message": str(failure),
            }

        assert bridge._active_request_id is None
        assert bridge._stream_open is False
        assert bridge._cancel_requested is False
        assert list(bridge._events) == []
        assert session.submit_count == 0
        assert session.accepted_count == 0
        assert session.busy is False

        accepted = bridge.submit("segunda tentativa")
        assert accepted == {"accepted": True, "request_id": "request-2"}
        terminal = bridge.wait_events(timeout_ms=1_000)
        assert terminal[0]["type"] == "settled"
        assert terminal[0]["request_id"] == "request-2"
        assert session.submit_count == 1
        assert session.accepted_count == 1
        assert session.consumed_count == 1
        assert session.busy is False
    finally:
        bridge.close()


def test_submit_exception_discards_prior_deltas_then_consumes_next_terminal() -> None:
    session = _Session(OSError("submission failed"))
    bridge = WorkbenchBridge(session=session)
    try:
        with pytest.raises(OSError, match="submission failed"):
            bridge.submit("falha antes de aceitar")
        assert bridge.wait_events(timeout_ms=0) == []
        assert bridge._active_request_id is None
        assert session.busy is False

        accepted = bridge.submit("segunda tentativa")
        terminal = bridge.wait_events(timeout_ms=1_000)
        assert accepted == {"accepted": True, "request_id": "request-2"}
        assert terminal[-1]["type"] == "settled"
        assert terminal[-1]["request_id"] == "request-2"
        assert all(event.get("text") != "orphan delta" for event in terminal)
        assert session.accepted_count == 1
        assert session.consumed_count == 1
        assert session.busy is False
    finally:
        bridge.close()


@pytest.mark.parametrize("failure", [RuntimeError("owner busy"), ValueError("request invalid")])
def test_synchronous_owner_rejections_remain_rejections(failure: Exception) -> None:
    session = _Session(failure)
    bridge = WorkbenchBridge(session=session)
    try:
        rejected = bridge.submit("solicitação rejeitada")
        assert rejected == {"accepted": False, "message": str(failure)}
        assert bridge.wait_events(timeout_ms=0) == []
        assert bridge._active_request_id is None
        assert session.busy is False

        accepted = bridge.submit("segunda tentativa")
        terminal = bridge.wait_events(timeout_ms=1_000)
        assert accepted == {"accepted": True, "request_id": "request-2"}
        assert terminal[-1]["type"] == "settled"
        assert session.accepted_count == 1
        assert session.consumed_count == 1
        assert session.busy is False
    finally:
        bridge.close()


def test_cancel_after_failed_collector_does_not_target_old_request_and_close_is_safe(monkeypatch) -> None:
    real_thread = threading.Thread
    attempts = 0

    class FailFirstStart(real_thread):
        def start(self) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("collector unavailable")
            super().start()

    monkeypatch.setattr(webview.threading, "Thread", FailFirstStart)
    session = _Session(hold_settlement=True)
    bridge = WorkbenchBridge(session=session)
    try:
        with pytest.raises(OSError, match="collector unavailable"):
            bridge.submit("collector falhou")
        assert bridge.cancel("request-1")["accepted"] is False
        assert session.cancel_count == 0

        accepted = bridge.submit("execução seguinte")
        assert accepted["accepted"] is True
        assert session.busy is True
        assert bridge.cancel(accepted["request_id"]) == {
            "accepted": True,
            "request_id": accepted["request_id"],
        }
        events = bridge.wait_events(timeout_ms=1_000)
        if not any(event["type"] == "settled" for event in events):
            events.extend(bridge.wait_events(timeout_ms=1_000))
        assert [event["type"] for event in events] == ["cancel_requested", "settled"]
        assert session.cancel_count == 1
        assert session.consumed_count == 1
        assert session.busy is False
    finally:
        bridge.close()


def test_close_after_failed_collector_has_no_owner_execution(monkeypatch) -> None:
    real_thread = threading.Thread

    class FailStart(real_thread):
        def start(self) -> None:
            raise OSError("collector unavailable")

    monkeypatch.setattr(webview.threading, "Thread", FailStart)
    session = _Session()
    bridge = WorkbenchBridge(session=session)
    with pytest.raises(OSError, match="collector unavailable"):
        bridge.submit("collector falhou")
    assert bridge.close() == {"closed": True}
    assert session.submit_count == 0
    assert session.close_count == 1
    assert session.busy is False


def test_close_failure_keeps_admission_blocked_and_cleanup_retryable() -> None:
    class CloseFailsOnce(_Session):
        def close(self) -> Future:
            self.close_count += 1
            future: Future = Future()
            if self.close_count == 1:
                future.set_exception(TimeoutError("session cleanup timed out"))
            else:
                future.set_result(None)
            return future

    session = CloseFailsOnce()
    bridge = WorkbenchBridge(session=session)

    with pytest.raises(TimeoutError, match="session cleanup timed out"):
        bridge.close()

    assert bridge._closed is True
    assert bridge._close_complete is False
    assert bridge.submit("must stay blocked")["accepted"] is False
    assert bridge.close() == {"closed": True}
    assert session.close_count == 2
    assert bridge._close_complete is True
    assert bridge.close() == {"closed": True}
    assert session.close_count == 2


@pytest.mark.parametrize(
    "failure_type", [OSError, TimeoutError, RuntimeError, ValueError],
    ids=["os-error", "timeout", "runtime-error", "value-error"],
)
def test_exception_after_owner_admission_keeps_settlement_consumer_and_terminal(
    monkeypatch, tmp_path, failure_type
) -> None:
    from llm_agent.application.context import AppPaths
    from llm_agent.application.task_execution import TaskExecutionStart
    from llm_agent.interactive.session import InteractiveSession

    start = TaskExecutionStart(
        tmp_path,
        AppPaths.discover(tmp_path / "home", env={}),
        configure_logging=False,
    )
    session = InteractiveSession(start)
    bridge = WorkbenchBridge(session=session)
    release = threading.Event()
    executing = threading.Event()
    executed: list[str] = []
    controller_identity = id(session.controller)

    def fail_begin_run(*_args, **_kwargs) -> None:
        raise failure_type("view initialization failed after admission")

    monkeypatch.setattr(session.view, "begin_run", fail_begin_run)

    def operation(*_args):
        executed.append("started")
        executing.set()
        assert release.wait(2)
        return {"status": "succeeded", "answer": "terminal retained"}, None

    def request(_request_id, on_admitted=None):
        options = {"on_admitted": on_admitted} if on_admitted is not None else {}
        return session._controlled(operation, owner="post-admission-test", **options)

    try:
        try:
            accepted = bridge._start_request(request)
        except Exception as exc:
            pytest.fail(f"post-admission exception escaped without a settlement handoff: {exc}")
        assert accepted == {"accepted": True, "request_id": "request-1"}
        assert executing.wait(2)
        assert session.controller.is_busy()
        assert bridge._active_request_id == "request-1"
        assert id(session.controller) == controller_identity
        assert bridge._start_request(request)["accepted"] is False

        release.set()
        events = bridge.wait_events(timeout_ms=1_000)
        assert events[-1]["type"] == "error"
        assert events[-1]["request_id"] == "request-1"
        assert events[-1]["message"] == "view initialization failed after admission"
        assert events[-1]["settlement"]["observation"] == {
            "status": "succeeded",
            "answer": "terminal retained",
        }
        assert executed == ["started"]
        assert session.controller.is_busy() is False
        assert session.controller.current_generation is None
        assert session._active is not None and session._active.done()
        assert session.controller.poll_result() is None

        monkeypatch.setattr(session.view, "begin_run", lambda *_args, **_kwargs: None)
        accepted_again = bridge._start_request(request)
        assert accepted_again == {"accepted": True, "request_id": "request-2"}
        final = bridge.wait_events(timeout_ms=1_000)
        assert final[-1]["type"] == "settled"
        assert final[-1]["request_id"] == "request-2"
        assert executed == ["started", "started"]
        assert session.controller.is_busy() is False
    finally:
        release.set()
        if session.controller.is_busy():
            session.controller.wait_for_settlement(timeout_seconds=2)
        bridge.close()


@pytest.mark.parametrize(
    "failure", [OSError("late I/O error"), TimeoutError("late timeout"), RuntimeError("late rejection"), ValueError("late invalid state")],
    ids=["os-error", "timeout", "runtime-error", "value-error"],
)
def test_bridge_consumes_settlement_when_start_raises_after_handoff(failure: Exception) -> None:
    session = _Session(hold_settlement=True)
    bridge = WorkbenchBridge(session=session)
    future = _ConsumingFuture(session)

    def start(_request_id, on_admitted):
        session.busy = True
        session.accepted_count += 1
        session.current_future = future
        future.set_result(({"status": "succeeded", "answer": "accepted before exception"}, None))
        on_admitted(future)
        raise failure

    try:
        assert bridge._start_request(start) == {"accepted": True, "request_id": "request-1"}
        events = bridge.wait_events(timeout_ms=1_000)
        assert events[-1] == {
            "type": "error",
            "request_id": "request-1",
            "message": str(failure),
            "settlement": {"observation": {"status": "succeeded", "answer": "accepted before exception"}},
        }
        assert session.busy is False
        assert session.consumed_count == 1
        assert bridge._active_request_id is None

        session.hold_settlement = False
        accepted = bridge.submit("next request")
        assert accepted == {"accepted": True, "request_id": "request-2"}
        terminal = bridge.wait_events(timeout_ms=1_000)
        assert terminal[-1]["type"] == "settled"
        assert session.accepted_count == 2
        assert session.consumed_count == 2
    finally:
        bridge.close()


def test_cancel_during_collector_preparation_is_applied_before_worker_runs(monkeypatch, tmp_path) -> None:
    from llm_agent.application.context import AppPaths
    from llm_agent.application.task_execution import TaskExecutionStart
    from llm_agent.interactive.session import InteractiveSession

    start = TaskExecutionStart(
        tmp_path,
        AppPaths.discover(tmp_path / "home", env={}),
        configure_logging=False,
    )
    session = InteractiveSession(start)
    bridge = WorkbenchBridge(session=session)
    real_thread = threading.Thread
    collector_entered = threading.Event()
    release_collector = threading.Event()
    operation_started = threading.Event()
    cancel_seen: list[bool] = []
    result_holder: list[dict[str, object]] = []
    submit_errors: list[BaseException] = []

    class BlockCollectorStart(real_thread):
        def start(self) -> None:
            if self.name == "workbench-ui-events":
                collector_entered.set()
                assert release_collector.wait(2)
            super().start()

    def operation(_envelope, cancel_event, _register):
        operation_started.set()
        cancel_seen.append(cancel_event.is_set())
        status = "cancelled" if cancel_event.is_set() else "succeeded"
        return {"status": status, "reason_code": "INTERACTION_CANCELLED"}, None

    def request(_request_id, on_admitted=None):
        return session._controlled(operation, owner="pre-admission-cancel", track_activity=False, on_admitted=on_admitted)

    def submit_request():
        try:
            result_holder.append(bridge._start_request(request))
        except BaseException as exc:
            submit_errors.append(exc)

    submitter = real_thread(target=submit_request, name="submit-caller")
    monkeypatch.setattr(webview.threading, "Thread", BlockCollectorStart)
    try:
        submitter.start()
        assert collector_entered.wait(2)
        assert bridge.cancel("request-1") == {"accepted": True, "request_id": "request-1"}
        assert session.controller.is_busy() is False
        assert operation_started.is_set() is False
        assert bridge.wait_events(timeout_ms=0) == [{"type": "cancel_requested", "request_id": "request-1"}]

        release_collector.set()
        submitter.join(2)
        assert submitter.is_alive() is False
        assert submit_errors == []
        assert result_holder == [{"accepted": True, "request_id": "request-1"}]
        assert operation_started.wait(2)
        events = bridge.wait_events(timeout_ms=1_000)
        assert [event["type"] for event in events] == ["settled"]
        assert events[-1]["observation"]["status"] == "cancelled"
        assert cancel_seen == [True]
        assert session.controller.is_busy() is False
        assert session._active is not None and session._active.done()
        assert session.controller.poll_result() is None
    finally:
        release_collector.set()
        if submitter.is_alive():
            submitter.join(2)
        bridge.close()


def test_close_during_active_task_cancels_and_consumes_terminal(tmp_path) -> None:
    from llm_agent.application.context import AppPaths
    from llm_agent.application.task_execution import TaskExecutionStart
    from llm_agent.interactive.session import InteractiveSession

    start = TaskExecutionStart(
        tmp_path,
        AppPaths.discover(tmp_path / "home", env={}),
        configure_logging=False,
    )
    session = InteractiveSession(start)
    bridge = WorkbenchBridge(session=session)
    operation_started = threading.Event()
    cancel_seen: list[bool] = []
    close_results: list[dict[str, bool]] = []
    close_errors: list[BaseException] = []

    def operation(_envelope, cancel_event, _register):
        operation_started.set()
        assert cancel_event.wait(2)
        cancel_seen.append(cancel_event.is_set())
        return {"status": "cancelled", "reason_code": "INTERACTION_CANCELLED"}, None

    def request(_request_id, on_admitted=None):
        return session._controlled(operation, owner="close-active-task", track_activity=False, on_admitted=on_admitted)

    assert bridge._start_request(request) == {"accepted": True, "request_id": "request-1"}
    assert operation_started.wait(2)

    def close_bridge():
        try:
            close_results.append(bridge.close())
        except BaseException as exc:
            close_errors.append(exc)

    closer = threading.Thread(target=close_bridge, name="close-caller")
    closer.start()
    closer.join(3)
    assert closer.is_alive() is False
    assert close_errors == []
    assert close_results == [{"closed": True}]
    assert cancel_seen == [True]
    assert session._active is not None and session._active.done()
    assert session.controller.is_busy() is False
    assert session.controller.poll_result() is None
    assert bridge._active_request_id is None


def test_owner_thread_start_raising_after_launch_is_drained_before_request_fails(monkeypatch, tmp_path) -> None:
    from llm_agent.application.context import AppPaths
    from llm_agent.application.task_execution import TaskExecutionStart
    from llm_agent.interactive.session import InteractiveSession

    start = TaskExecutionStart(
        tmp_path,
        AppPaths.discover(tmp_path / "home", env={}),
        configure_logging=False,
    )
    session = InteractiveSession(start)
    bridge = WorkbenchBridge(session=session)
    controller_identity = id(session.controller)
    real_start = threading.Thread.start
    release = threading.Event()
    operation_started = threading.Event()
    executed: list[str] = []

    def start_then_raise(thread):
        result = real_start(thread)
        if thread.name.startswith("interactive-agent-worker-"):
            raise OSError("Thread.start raised after the worker launched")
        return result

    def operation(*_args):
        executed.append("worker")
        operation_started.set()
        assert release.wait(2)
        return {"status": "succeeded", "answer": "worker terminal"}, None

    def request(_request_id, on_admitted=None):
        return session._controlled(
            operation,
            owner="thread-start-after-launch",
            track_activity=False,
            on_admitted=on_admitted,
        )

    monkeypatch.setattr(threading.Thread, "start", start_then_raise)
    try:
        assert bridge._start_request(request) == {"accepted": True, "request_id": "request-1"}
        assert operation_started.wait(2)
        assert session.controller.is_busy()
        assert id(session.controller) == controller_identity

        release.set()
        events = bridge.wait_events(timeout_ms=1_000)
        assert events[-1]["type"] == "error"
        assert events[-1]["message"] == "Thread.start raised after the worker launched"
        assert events[-1]["settlement"]["observation"] == {
            "status": "succeeded",
            "answer": "worker terminal",
        }
        assert executed == ["worker"]
        assert session.controller.is_busy() is False
        assert session._active is not None and session._active.done()
        assert session.controller.poll_result() is None

        monkeypatch.setattr(threading.Thread, "start", real_start)
        assert bridge._start_request(request) == {"accepted": True, "request_id": "request-2"}
        assert bridge.wait_events(timeout_ms=1_000)[-1]["type"] == "settled"
        assert executed == ["worker", "worker"]
        assert session.controller.is_busy() is False
    finally:
        release.set()
        if session.controller.is_busy():
            session.controller.wait_for_settlement(timeout_seconds=2)
        bridge.close()


def test_stream_callback_error_after_terminal_keeps_consumed_observation(tmp_path) -> None:
    from llm_agent.application.context import AppPaths
    from llm_agent.application.task_execution import TaskExecutionStart
    from llm_agent.interactive.session import InteractiveSession

    start = TaskExecutionStart(
        tmp_path,
        AppPaths.discover(tmp_path / "home", env={}),
        configure_logging=False,
    )
    session = InteractiveSession(start)
    bridge = WorkbenchBridge(session=session)

    def fail_callback(_chunk):
        raise RuntimeError("stream callback failed after terminal")

    def operation(envelope, _cancel, _register):
        session.controller.stream_channel.publish(envelope.run_generation, "assistant", "chunk before callback error")
        return {"status": "succeeded", "answer": "settlement preserved"}, None

    def request(_request_id, on_admitted=None):
        return session._controlled(
            operation,
            stream_callback=fail_callback,
            owner="callback-error-test",
            track_activity=False,
            on_admitted=on_admitted,
        )

    try:
        assert bridge._start_request(request) == {"accepted": True, "request_id": "request-1"}
        event = bridge.wait_events(timeout_ms=1_000)[-1]
        assert event["type"] == "error"
        assert event["message"] == "stream callback failed after terminal"
        assert event["settlement"]["observation"] == {
            "status": "succeeded",
            "answer": "settlement preserved",
        }
        assert session.controller.is_busy() is False
        assert session._active is not None and session._active.done()
        assert session.controller.poll_result() is None
    finally:
        bridge.close()


def test_reference_projection_error_emits_one_terminal_and_recovers(monkeypatch) -> None:
    from types import SimpleNamespace

    session = _Session(hold_settlement=True)
    bridge = WorkbenchBridge(session=session)
    reference = SimpleNamespace(
        title="Valid report",
        source="workspace",
        run_id="run-1",
        workspace_id="workspace-1",
        artifact_id="artifact-1",
        kind="report",
        completeness="complete",
    )
    observation = {"status": "succeeded", "answer": "terminal retained"}
    original_metadata = bridge._reference_metadata
    projected: list[object] = []

    def fail_projection(_token, value, _operation=None):
        assert value is reference
        projected.append(value)
        raise AttributeError("injected reference projection failure")

    def start(request_id, on_admitted):
        assert session.busy is False
        session.busy = True
        session.accepted_count += 1
        future = _ConsumingFuture(session)
        session.current_future = future
        on_admitted(future)
        answer = observation if request_id == "request-1" else {
            "status": "succeeded",
            "answer": "second request",
        }
        future.set_result((answer, reference))
        return future

    monkeypatch.setattr(bridge, "_reference_metadata", fail_projection)
    try:
        assert bridge._start_request(start) == {"accepted": True, "request_id": "request-1"}
        first_events = bridge.wait_events(timeout_ms=1_000)
        assert len(first_events) == 1
        assert first_events[0] == {
            "type": "error",
            "request_id": "request-1",
            "message": "injected reference projection failure",
            "settlement": {"observation": observation},
        }
        assert projected == [reference]
        assert session.busy is False
        assert session.consumed_count == 1
        assert bridge._active_request_id is None
        assert bridge._stream_open is False
        assert bridge._references == {}
        assert bridge.wait_events(timeout_ms=0) == []

        monkeypatch.setattr(bridge, "_reference_metadata", original_metadata)
        assert bridge._start_request(start) == {"accepted": True, "request_id": "request-2"}
        second_events = bridge.wait_events(timeout_ms=1_000)
        assert len(second_events) == 1
        assert second_events[0]["type"] == "settled"
        assert second_events[0]["request_id"] == "request-2"
        assert second_events[0]["observation"]["answer"] == "second request"
        assert second_events[0]["reference"]["artifact_id"] == "artifact-1"
        assert session.accepted_count == 2
        assert session.consumed_count == 2
        assert session.busy is False
        assert bridge._active_request_id is None
        assert bridge.wait_events(timeout_ms=0) == []
    finally:
        bridge.close()


def test_owner_settlement_exception_is_terminal_and_does_not_orphan_request() -> None:
    session = _Session(hold_settlement=True)
    bridge = WorkbenchBridge(session=session)

    def start(_request_id, on_admitted):
        assert session.busy is False
        session.busy = True
        session.accepted_count += 1
        future = _ConsumingFuture(session)
        session.current_future = future
        on_admitted(future)
        future.set_exception(OSError("owner settlement failed"))
        return future

    try:
        assert bridge._start_request(start) == {"accepted": True, "request_id": "request-1"}
        events = bridge.wait_events(timeout_ms=1_000)
        assert events == [{
            "type": "error",
            "request_id": "request-1",
            "message": "owner settlement failed",
        }]
        assert session.busy is False
        assert session.consumed_count == 1
        assert bridge._active_request_id is None
        assert bridge._stream_open is False
        assert bridge.wait_events(timeout_ms=0) == []
    finally:
        bridge.close()


@pytest.mark.parametrize(
    "failure",
    [OSError("owner worker start failed"), TimeoutError("owner worker start timed out")],
    ids=["os-error", "timeout"],
)
def test_owner_worker_start_exception_restores_real_controller_and_bridge(
    tmp_path, monkeypatch, failure
) -> None:
    from llm_agent.application.context import AppPaths
    from llm_agent.application.task_execution import TaskExecutionStart
    from llm_agent.interactive.session import InteractiveSession

    start = TaskExecutionStart(
        tmp_path,
        AppPaths.discover(tmp_path / "home", env={}),
        configure_logging=False,
    )
    session = InteractiveSession(start)
    bridge = WorkbenchBridge(session=session)
    real_start = threading.Thread.start
    attempts = 0
    executed = []

    def fail_owner_start(thread):
        nonlocal attempts
        attempts += 1
        # The bridge collector is started first; the shared owner worker is
        # the second thread and fails before an OS thread is created.
        if attempts == 2:
            raise failure
        return real_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_owner_start)

    def request(_request_id, on_admitted=None):
        return session._controlled(
            lambda *_args: (executed.append("worker") or {"status": "succeeded", "answer": "ok"}, None),
            owner="workbench-test",
            track_activity=False,
            on_admitted=on_admitted,
        )

    try:
        with pytest.raises(type(failure), match=str(failure)):
            bridge._start_request(request)

        assert bridge._active_request_id is None
        assert bridge._stream_open is False
        assert session.controller.is_busy() is False
        assert session.controller.current_generation is None
        assert executed == []

        assert bridge._start_request(request) == {"accepted": True, "request_id": "request-2"}
        terminal = bridge.wait_events(timeout_ms=1_000)
        assert terminal[-1]["type"] == "settled"
        assert terminal[-1]["request_id"] == "request-2"
        assert executed == ["worker"]
        assert session.controller.is_busy() is False
        assert session._active.done()
        assert bridge._active_request_id is None
    finally:
        bridge.close()
