from __future__ import annotations

import time
from threading import Event
from types import SimpleNamespace

from agent.interfaces.cli.controller import (
    ControllerState,
    InteractiveExecutionController,
    SubmissionEnvelope,
    WorkerSettlementTimeout,
)


def _envelope(text: str, *, command_id: str = "natural_text") -> SubmissionEnvelope:
    return SubmissionEnvelope(
        0,
        text,
        command_id,
        "AGENTIC",
        "AGENTIC_SUBMIT",
        "PENDING_EXACT_TEXT",
        text,
        "natural",
        "fake.owner",
    )


def test_second_worker_is_impossible_and_busy_text_is_pending_without_execution() -> None:
    controller = InteractiveExecutionController()
    release = Event()
    calls: list[str] = []

    def execute(envelope, _cancel, _register):
        calls.append(envelope.visible_text)
        release.wait(5)
        return "done"

    first = controller.submit(_envelope("first"), execute)
    second = controller.submit(_envelope("follow-up"), execute)
    assert first.disposition == "ACCEPTED"
    assert second.disposition == "PENDING_FOLLOWUP"
    assert controller.state is ControllerState.RUNNING
    assert calls == ["first"]
    assert [item.visible_text for item in controller.pending.list()] == ["follow-up"]
    release.set()
    assert controller.wait_for_settlement().result == "done"
    assert controller.state is ControllerState.IDLE


def test_pending_send_is_consume_on_accept_and_idempotent() -> None:
    controller = InteractiveExecutionController()
    gate = Event()
    calls: list[str] = []

    def execute(envelope, _cancel, _register):
        calls.append(envelope.payload)
        gate.wait(5)
        return envelope.payload

    controller.submit(_envelope("first"), execute)
    pending = controller.submit(_envelope("later"), execute)
    gate.set()
    controller.wait_for_settlement()
    sent = controller.send_pending(pending.pending_id or 0, execute)
    duplicate = controller.send_pending(pending.pending_id or 0, execute)
    assert sent.disposition == "ACCEPTED"
    assert duplicate.disposition == "IGNORED_DUPLICATE"
    assert controller.wait_for_settlement().result == "later"
    assert calls == ["first", "later"]


def test_cancel_is_request_only_and_stale_generation_cannot_touch_new_run() -> None:
    controller = InteractiveExecutionController()
    started = Event()
    release = Event()
    terminalized: list[str] = []
    callbacks: list[callable] = []

    def execute(_envelope, cancel_event, register):
        register(lambda: callbacks.append("request"))
        started.set()
        release.wait(5)
        assert cancel_event.is_set()
        return "cancelled-by-runtime"

    accepted = controller.submit(_envelope("cancel-me"), execute)
    assert started.wait(2)
    requested = controller.request_cancel(accepted.run_generation)
    assert requested.disposition == "CANCEL_REQUESTED"
    assert controller.state is ControllerState.CANCELLING
    assert terminalized == []
    assert callbacks == ["request"]
    release.set()
    assert controller.wait_for_settlement().result == "cancelled-by-runtime"

    second = controller.submit(_envelope("new"), lambda *_args: "new-result")
    stale = controller.request_cancel(accepted.run_generation)
    assert stale.disposition == "IGNORED_STALE"
    assert controller.wait_for_settlement().result == "new-result"
    assert second.run_generation != accepted.run_generation


def test_worker_exception_is_returned_and_controller_recovers() -> None:
    controller = InteractiveExecutionController()

    def explode(*_args):
        raise RuntimeError("provider exploded")

    controller.submit(_envelope("bad"), explode)
    message = controller.wait_for_settlement()
    assert message is not None
    assert isinstance(message.error, RuntimeError)
    assert controller.state is ControllerState.IDLE


def test_pending_bounds_reject_without_silent_eviction() -> None:
    from agent.interfaces.cli.controller import PendingStore

    store = PendingStore(max_items=1, max_item_chars=4, max_total_chars=4)
    store.add(_envelope("one"))
    try:
        controller = InteractiveExecutionController(pending=store)
        controller.submit(_envelope("two"), lambda *_args: None)
        assert len(store.list()) == 1
    finally:
        # Keep this test independent of thread scheduling.
        time.sleep(0)


def test_shutdown_reports_unsettled_non_daemon_worker_with_a_bound() -> None:
    controller = InteractiveExecutionController()
    started = Event()
    release = Event()

    def execute(_envelope, _cancel, _register):
        started.set()
        release.wait(5)
        return "settled"

    accepted = controller.submit(_envelope("slow"), execute)
    assert accepted.disposition == "ACCEPTED"
    assert started.wait(2)
    message = controller.wait_for_settlement(timeout_seconds=0.01)
    assert message is not None and isinstance(message.error, WorkerSettlementTimeout)
    assert controller.is_busy()
    release.set()
    settled = controller.wait_for_settlement(timeout_seconds=2)
    assert settled is not None and settled.result == "settled"


def test_shutdown_order_closes_application_before_releasing_shell() -> None:
    from agent.interfaces.cli import interactive_resources

    order: list[str] = []
    resources = interactive_resources._SessionResources()
    application = SimpleNamespace(close=lambda: order.append("application"))
    shell = SimpleNamespace(close=lambda: order.append("shell"))

    interactive_resources.settle(resources, None, shell, application)

    assert order == ["application", "shell"]


def test_shutdown_settles_worker_and_query_before_detaching_ui_sink() -> None:
    from agent.interfaces.cli import interactive_resources

    order: list[str] = []

    class _Query:
        def cancel_and_wait(self):
            order.extend(("query-cancel-request", "query-settled"))
            return None

    class _Controller:
        def shutdown(self):
            order.extend(("agent-cancel-request", "worker-settled"))
            return None

    sink = object()
    resources = interactive_resources._SessionResources(
        controller=_Controller(),
        query_executor=_Query(),
        event_dispatcher=SimpleNamespace(remove_sink=lambda value: order.append("ui-sink-detached")),
        event_sink=sink,
    )
    application = SimpleNamespace(close=lambda: order.append("application"))
    shell = SimpleNamespace(close=lambda: order.append("shell"))

    status = interactive_resources.settle(resources, None, shell, application)

    assert status.settled
    assert order == [
        "query-cancel-request",
        "query-settled",
        "agent-cancel-request",
        "worker-settled",
        "ui-sink-detached",
        "application",
        "shell",
    ]


def test_noncooperative_worker_and_query_return_truthful_failed_shutdown_status(tmp_path) -> None:
    from agent.application_services.queries import (
        WorkspaceQueryKind,
        WorkspaceQueryRequest,
        WorkspaceQueryResult,
        WorkspaceQueryStatus,
    )
    from agent.interfaces.cli import interactive_resources
    from agent.interfaces.cli.query_executor import BoundedQueryExecutor
    from agent.runtime.workspace_context import WorkspaceContext

    controller = InteractiveExecutionController()
    worker_started = Event()
    worker_release = Event()

    def worker(_envelope, _cancel, _register):
        worker_started.set()
        worker_release.wait(5)
        return "worker-settled"

    assert controller.submit(_envelope("worker"), worker).disposition == "ACCEPTED"
    assert worker_started.wait(2)

    workspace = WorkspaceContext.create(tmp_path)
    query_executor = BoundedQueryExecutor(workspace_id=workspace.workspace_id)
    query_started = Event()
    query_release = Event()

    def query(request, _cancel):
        query_started.set()
        query_release.wait(5)
        return WorkspaceQueryResult(request.kind, WorkspaceQueryStatus.SUCCEEDED, data="query-settled")

    assert query_executor.submit(
        WorkspaceQueryRequest(WorkspaceQueryKind.FIND, {}),
        task_active=False,
        execute=query,
    )
    assert query_started.wait(2)
    order: list[str] = []
    sink = object()
    shell = SimpleNamespace(
        print_background=lambda value: order.append(str(value)),
        close=lambda: order.append("shell"),
    )
    application = SimpleNamespace(close=lambda: order.append("application"))
    context = SimpleNamespace(shell=shell, controller=controller, query_executor=query_executor)
    resources = interactive_resources._SessionResources(
        controller=controller,
        query_executor=query_executor,
        event_dispatcher=SimpleNamespace(remove_sink=lambda _value: order.append("ui-sink-detached")),
        event_sink=sink,
    )

    failed = interactive_resources.settle(
        resources,
        context,
        shell,
        application,
        timeout_seconds=0.01,
    )
    assert not failed.settled and failed.exit_code == 1
    assert "ui-sink-detached" not in order
    assert "application" not in order and "shell" not in order
    assert controller.is_busy() and query_executor.is_busy()

    worker_release.set()
    query_release.set()
    assert controller.wait_for_settlement(timeout_seconds=2) is not None
    assert query_executor.cancel_and_wait(timeout_seconds=2) is not None
    settled = interactive_resources.settle(resources, context, shell, application, timeout_seconds=2)
    assert settled.settled
    assert order[-3:] == ["ui-sink-detached", "application", "shell"]


def test_run_chat_propagates_failed_shutdown_status_as_nonzero_exit(monkeypatch) -> None:
    from agent.interfaces.cli import interactive_session

    monkeypatch.setattr(interactive_session.first_run, "is_interactive_terminal", lambda: True)
    monkeypatch.setattr(
        interactive_session.first_run,
        "prepare_chat_workspace",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        interactive_session,
        "_run_application_session",
        lambda *args, **kwargs: interactive_session.InteractiveSessionResult(
            None,
            None,
            interactive_session.interactive_resources.ShutdownStatus.failed("WORKER_SHUTDOWN_TIMEOUT"),
        ),
    )
    args = SimpleNamespace(workspace=None, config=None, home=None, profile=None)

    result = interactive_session.run_chat(
        args,
        value=lambda _args, _name, _default=None: _default,
        app_paths=lambda _args: None,
        create_application=lambda *_args, **_kwargs: SimpleNamespace(),
        context_from_application=lambda *_args, **_kwargs: None,
    )

    assert result == 1
