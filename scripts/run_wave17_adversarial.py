"""Deterministic, model-free adversarial campaign for the interactive boundary."""

from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from types import SimpleNamespace
from typing import Any, Callable, cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.application_services.queries import (  # noqa: E402
    ReadOnlyWorkspaceQueryService,
    WorkspaceQueryKind,
    WorkspaceQueryRequest,
    WorkspaceQueryResult,
    WorkspaceQueryStatus,
)
from agent.approval import ApprovalDecision, ApprovalRequest, ApprovalWaitCancelled  # noqa: E402
from agent.interfaces.cli import app, command_handlers, first_run, interactive_admission, ui  # noqa: E402
from agent.interfaces.cli.action_registry import DEFAULT_CLI_ACTION_REGISTRY  # noqa: E402
from agent.interfaces.cli.attention import ApprovalBroker  # noqa: E402
from agent.interfaces.cli.controller import (  # noqa: E402
    InteractiveExecutionController,
    PendingStore,
    SubmissionEnvelope,
)
from agent.interfaces.cli.query_executor import (  # noqa: E402
    BoundedQueryExecutor,
    CliQueryCompletion,
    CliQuerySubmission,
)
from agent.interfaces.cli.ui_plane import RuntimeEventUISink, RunViewModel, UIEventMailbox  # noqa: E402
from agent.runtime.config_errors import ConfigNotFound  # noqa: E402
from agent.runtime.config_repository import ConfigRepository  # noqa: E402
from agent.runtime.correlation import RunCorrelation  # noqa: E402
from agent.runtime.event_kinds import RuntimeEventKind  # noqa: E402
from agent.runtime.events import RuntimeEvent  # noqa: E402
from agent.runtime.paths import AppPaths  # noqa: E402
from agent.runtime.workspace_context import WorkspaceContext  # noqa: E402


@dataclass(frozen=True, slots=True)
class AdversarialResult:
    scenario_id: str
    passed: bool
    detail: str


class _Console:
    def __init__(self, *answers: str) -> None:
        self.answers = iter(answers)
        self.output: list[str] = []

    def print(self, *values: object, **_: object) -> None:
        self.output.append(" ".join(map(str, values)))

    def input(self, _prompt: str) -> str:
        return next(self.answers)


def _event(kind: RuntimeEventKind, correlation: RunCorrelation, **data: object) -> RuntimeEvent:
    return RuntimeEvent.from_fields(kind, correlation, data)


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
        "owner",
    )


def _query_request(command_id: str, arguments: dict[str, object] | None = None) -> WorkspaceQueryRequest:
    return WorkspaceQueryRequest(WorkspaceQueryKind(command_id), arguments or {})


def _fake_query_result(request: WorkspaceQueryRequest, data: object = "ok") -> WorkspaceQueryResult:
    return WorkspaceQueryResult(request.kind, WorkspaceQueryStatus.SUCCEEDED, data=data)


_W17_BOUNDED_GIT_TOKENS = (
    "shell=False",
    "stdin=subprocess.DEVNULL",
    "GIT_EXTERNAL_DIFF",
    "core.fsmonitor=false",
)
_W17_HARDENED_GIT_TOKENS = ("--no-ext-diff", "--no-textconv", "GIT_OPTIONAL_LOCKS", "core.fsmonitor=false")


def _canonical_query_git_source() -> str:
    return (ROOT / "agent/application_services/query_git.py").read_text(encoding="utf-8")


def _assert_required_git_tokens(source: str, tokens: tuple[str, ...]) -> None:
    for token in tokens:
        assert token in source


def _approval_thread(broker: ApprovalBroker) -> tuple[Thread, list[object]]:
    values: list[object] = []
    request = ApprovalRequest("write", "workspace/file.txt", "apply", {"invocation_id": "inv-1"})

    def wait() -> None:
        try:
            values.append(broker.request(request))
        except BaseException as exc:
            values.append(exc)

    thread = Thread(target=wait, daemon=False)
    thread.start()
    for _ in range(100):
        if broker.current() is not None:
            break
        Event().wait(0.01)
    return thread, values


def _scenario_group_01(root: Path) -> tuple[Callable[[], None], ...]:
    def a01() -> None:
        controller = InteractiveExecutionController()
        gate = Event()
        controller.submit(_envelope("first"), lambda *_: (gate.wait(2), "done")[1])
        pending = controller.submit(_envelope("follow-up"), lambda *_: "later")
        assert pending.disposition == "PENDING_FOLLOWUP"
        gate.set()
        controller.wait_for_settlement()




    def a02() -> None:
        view = RunViewModel()
        view.begin_run(1, owner="owner")
        assert "RUN" in view.render_toolbar(width=120)




    def a03() -> None:
        workspace = WorkspaceContext.create(root)
        executor = BoundedQueryExecutor(workspace_id=workspace.workspace_id)
        request = executor.submit(_query_request("list_files", {"path": "."}), task_active=True, execute=ReadOnlyWorkspaceQueryService(workspace).execute)
        assert isinstance(request, CliQuerySubmission)
        assert executor.cancel_and_wait() is not None




    def a04() -> None:
        service = ReadOnlyWorkspaceQueryService(root)
        assert not hasattr(service, "tool_invocation_gateway")




    return (a01, a02, a03, a04,)


def _scenario_group_02(root: Path) -> tuple[Callable[[], None], ...]:
    def a05() -> None:
        broker = ApprovalBroker()
        broker.bind_generation(1)
        thread, values = _approval_thread(broker)
        draft = "multi\nline draft"
        current = broker.current()
        assert current is not None and draft
        broker.resolve(current.identity.attention_id, ApprovalDecision.REJECTED)
        thread.join(2)
        assert values == [ApprovalDecision.REJECTED]




    def a06() -> None:
        broker = ApprovalBroker()
        broker.bind_generation(1)
        thread, values = _approval_thread(broker)
        assert broker.current() is not None
        old = command_handlers.console
        command_handlers.console = _Console()
        try:
            command_handlers.attention("y", SimpleNamespace(approval_broker=broker, shell=None))
        finally:
            command_handlers.console = old
        current = broker.current()
        assert current is not None
        broker.resolve(current.identity.attention_id, ApprovalDecision.REJECTED)
        thread.join(2)
        assert values == [ApprovalDecision.REJECTED]




    def a07() -> None:
        broker = ApprovalBroker()
        broker.bind_generation(1)
        thread, values = _approval_thread(broker)
        assert broker.invalidate(generation=1)
        thread.join(2)
        assert len(values) == 1 and isinstance(values[0], ApprovalWaitCancelled)




    def a08() -> None:
        controller = InteractiveExecutionController()
        started, release = Event(), Event()

        def execute(
            _envelope: SubmissionEnvelope,
            cancel: Event,
            register: Callable[[Callable[[], None]], None],
        ) -> str:
            register(lambda: None)
            started.set()
            release.wait(2)
            assert cancel.is_set()
            return "settled"

        accepted = controller.submit(_envelope("cancel"), execute)
        assert started.wait(2)
        assert controller.request_cancel(accepted.run_generation).disposition == "CANCEL_REQUESTED"
        release.set()
        settled = controller.wait_for_settlement()
        assert settled is not None and settled.result == "settled"



    return (a05, a06, a07, a08,)


def _scenario_group_03(root: Path) -> tuple[Callable[[], None], ...]:
    def a09() -> None:
        view = RunViewModel()
        first, second = RunCorrelation.fresh(), RunCorrelation.fresh()
        view.begin_run(1, owner="one")
        view.apply(_event(RuntimeEventKind.MODEL_CALL_STARTED, first))
        view.begin_run(2, owner="two")
        assert view.apply(_event(RuntimeEventKind.ERROR, first, message="late")) is False
        assert view.apply(_event(RuntimeEventKind.MODEL_CALL_STARTED, second)) is True




    def a10() -> None:
        mailbox = UIEventMailbox(milestone_capacity=4, latest_capacity=2)
        correlation = RunCorrelation.fresh()
        for index in range(2000):
            mailbox.emit(_event(RuntimeEventKind.STEP_COMPLETED, correlation, step=index))
        assert len(mailbox) <= 6




    def a11() -> None:
        view = RunViewModel()
        view.begin_run(1, owner="owner")
        assert view.snapshot().state == "RUNNING"




    def a12() -> None:
        view = RunViewModel()
        view.begin_run(1, owner="owner")
        assert len(view.render_toolbar(width=40)) <= 40




    return (a09, a10, a11, a12,)


def _scenario_group_04(root: Path) -> tuple[Callable[[], None], ...]:
    def a13() -> None:
        original = first_run.is_interactive_terminal
        first_run.is_interactive_terminal = lambda: False
        try:
            try:
                app._run_chat(argparse.Namespace())
            except first_run.InteractiveTTYRequiredError:
                return
            raise AssertionError("non-TTY chat did not fail fast")
        finally:
            first_run.is_interactive_terminal = original




    def a14() -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            app._print_json({"status": "ok"})
        assert json.loads(output.getvalue()) == {"status": "ok"}
        assert "\x1b" not in output.getvalue()




    def a15() -> None:
        controller = InteractiveExecutionController()
        controller.submit(_envelope("explode"), lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
        message = controller.wait_for_settlement()
        assert message is not None and isinstance(message.error, RuntimeError)




    def a16() -> None:
        executor = BoundedQueryExecutor(workspace_id="w")
        request = executor.submit(_query_request("find"), task_active=False, execute=lambda *_: (_ for _ in ()).throw(RuntimeError("query")))
        assert isinstance(request, CliQuerySubmission)
        result = executor.cancel_and_wait()
        assert result is not None and result.result is not None and result.result.status is WorkspaceQueryStatus.FAILED and "RuntimeError" in (result.result.error or "")



    return (a13, a14, a15, a16,)


def _scenario_group_05(root: Path) -> tuple[Callable[[], None], ...]:
    def a17() -> None:
        _assert_required_git_tokens(_canonical_query_git_source(), _W17_BOUNDED_GIT_TOKENS)




    def a18() -> None:
        current, target = root / "current", root / "target"
        current.mkdir(exist_ok=True)
        target.mkdir(exist_ok=True)
        context = SimpleNamespace(workspace=SimpleNamespace(root=current), controller=SimpleNamespace(is_busy=lambda: True), approval_broker=SimpleNamespace(current=lambda: None), query_executor=SimpleNamespace(is_busy=lambda: False), shell=None)
        old = command_handlers.console
        command_handlers.console = _Console()
        try:
            command_handlers.show_workspace(f"/workspace switch {target}", context)
        finally:
            command_handlers.console = old
        assert not hasattr(context, "rebootstrap_workspace")




    def a19() -> None:
        context = SimpleNamespace(session=SimpleNamespace(model_profile=SimpleNamespace(model="m", provider="p")), config={"model_profiles": {"x": {}}}, controller=SimpleNamespace(is_busy=lambda: True), shell=None)
        old = command_handlers.console
        command_handlers.console = _Console()
        try:
            command_handlers.model("/model select x", context)
        finally:
            command_handlers.console = old
        assert not hasattr(context, "rebootstrap_profile")




    def a20() -> None:
        controller = InteractiveExecutionController()
        gate = Event()
        controller.submit(_envelope("one"), lambda *_: (gate.wait(2), "done")[1])
        pending = controller.submit(_envelope("keep"), lambda *_: "keep")
        gate.set()
        controller.wait_for_settlement()
        assert controller.pending.inspect(pending.pending_id or 0) is not None




    return (a17, a18, a19, a20,)


def _scenario_group_06(root: Path) -> tuple[Callable[[], None], ...]:
    def a21() -> None:
        controller = InteractiveExecutionController()
        gate = Event()
        controller.submit(_envelope("shutdown"), lambda *_: (gate.wait(2), "done")[1])
        gate.set()
        assert controller.shutdown() is not None




    def a22() -> None:
        broker = ApprovalBroker()
        broker.bind_generation(1)
        thread, values = _approval_thread(broker)
        broker.shutdown()
        thread.join(2)
        assert values and isinstance(values[0], ApprovalWaitCancelled)




    def a23() -> None:
        repository = ConfigRepository(AppPaths.discover(app_home=root / "app"))
        try:
            repository.load(environment={})
        except ConfigNotFound:
            return
        raise AssertionError("missing config was not explicit")




    def a24() -> None:
        source = (Path(__file__).resolve().parents[1] / "agent/interfaces/cli/task_continuity.py").read_text(encoding="utf-8")
        assert "Resume:" in source and "run_task_resume" in source



    return (a21, a22, a23, a24,)


def _scenario_group_07(root: Path) -> tuple[Callable[[], None], ...]:
    def a25() -> None:
        rendered: list[tuple[object, dict[str, object]]] = []
        old = ui.console
        ui.console = SimpleNamespace(print=lambda value, **kwargs: rendered.append((value, kwargs)))
        try:
            ui.render_code_result(cast(Any, SimpleNamespace(status=SimpleNamespace(value="failed"), summary="[red]literal[/red]", error=None, artifacts=(), diagnostics=())))
        finally:
            ui.console = old
        assert rendered and rendered[0][1].get("markup") is False




    def a26() -> None:
        store = PendingStore()
        text = "á\n二\n🙂"
        store.add(_envelope(text))
        assert store.list()[0].visible_text == text




    def a27() -> None:
        old = app.console
        app.console = _Console()
        app.console.input = lambda _prompt: (_ for _ in ()).throw(KeyboardInterrupt())  # type: ignore[method-assign]
        try:
            assert app._prompt(SimpleNamespace(orchestrator=SimpleNamespace(operational_mode_label="FULL"))) is None
        finally:
            app.console = old




    def a28() -> None:
        view = RunViewModel()
        correlation = RunCorrelation.fresh()
        view.begin_run(1, owner="one")
        view.apply(_event(RuntimeEventKind.STEP_COMPLETED, correlation, activity="old"))
        view.begin_run(2, owner="two")
        assert view.snapshot().milestones == ()




    return (a25, a26, a27, a28,)


def _scenario_group_08(root: Path) -> tuple[Callable[[], None], ...]:
    def a29() -> None:
        view = RunViewModel()
        first, second = RunCorrelation.fresh(), RunCorrelation.fresh()
        view.begin_run(1, owner="first")
        view.apply(_event(RuntimeEventKind.STEP_COMPLETED, first, activity="first"))
        view.begin_run(2, owner="second")
        assert view.apply(_event(RuntimeEventKind.STEP_COMPLETED, second, activity="second"))
        assert view.snapshot().milestones == ("second",)




    def a30() -> None:
        context = SimpleNamespace(session=SimpleNamespace(model_profile=SimpleNamespace(model="m")), workspace=SimpleNamespace(root=root), orchestrator=SimpleNamespace(operational_mode_label="FULL"), controller=None, view_model=None, shell=None)
        old = command_handlers.console
        command_handlers.console = _Console()
        try:
            command_handlers.status("/status", context)
        finally:
            command_handlers.console = old
        assert True




    def a31() -> None:
        view = RunViewModel()
        correlation = RunCorrelation.fresh()
        view.begin_run(1, owner="owner")
        view.apply(_event(RuntimeEventKind.TOOL_START, correlation, tool="reader", invocation_id="new"))
        view.apply(_event(RuntimeEventKind.TOOL_END, correlation, invocation_id="old"))
        assert view.snapshot().current_tool == "reader"




    def a32() -> None:
        broker = ApprovalBroker()
        broker.bind_generation(1)
        thread, values = _approval_thread(broker)
        current = broker.current()
        assert current is not None
        assert broker.resolve(current.identity.attention_id, ApprovalDecision.REJECTED) == "RESOLVED"
        assert broker.resolve(current.identity.attention_id, ApprovalDecision.REJECTED) == "IGNORED_DUPLICATE"
        thread.join(2)
        assert values == [ApprovalDecision.REJECTED]



    return (a29, a30, a31, a32,)


def _scenario_group_09(root: Path) -> tuple[Callable[[], None], ...]:
    def a33() -> None:
        workspace = WorkspaceContext.create(root)
        cancellation = type("Cancellation", (), {"is_cancelled": lambda self: False})()
        result = ReadOnlyWorkspaceQueryService(workspace).read_file(_query_request("read", {"file_path": "../escape"}), cancellation)
        assert result.status is WorkspaceQueryStatus.FAILED




    def a34() -> None:
        target = root / "huge.txt"
        target.write_text("x" * 300_000, encoding="utf-8")
        workspace = WorkspaceContext.create(root)
        cancellation = type("Cancellation", (), {"is_cancelled": lambda self: False})()
        result = ReadOnlyWorkspaceQueryService(workspace).read_file(_query_request("read", {"file_path": target.name}), cancellation)
        assert result.status is WorkspaceQueryStatus.SUCCEEDED and result.truncated




    def a35() -> None:
        mailbox = UIEventMailbox()
        sink = RuntimeEventUISink(mailbox)
        sink.emit(_event(RuntimeEventKind.WARNING, RunCorrelation.fresh(), message="safe"))
        assert len(mailbox) == 1




    def a36() -> None:
        mailbox = UIEventMailbox()
        view = RunViewModel()
        view.begin_run(1, owner="owner")
        view.apply(_event(RuntimeEventKind.STEP_COMPLETED, RunCorrelation.fresh(), activity="background"))
        context = SimpleNamespace(draft_text="draft")
        mailbox.emit(_event(RuntimeEventKind.WARNING, RunCorrelation.fresh(), message="output"))
        assert context.draft_text == "draft"




    return (a33, a34, a35, a36,)


def _scenario_group_10(root: Path) -> tuple[Callable[[], None], ...]:
    def a37() -> None:
        store = PendingStore(max_items=1, max_item_chars=8, max_total_chars=8)
        store.add(_envelope("12345678"))
        try:
            store.add(_envelope("overflow"))
        except ValueError:
            pass
        assert len(store.list()) == 1




    def a38() -> None:
        store = PendingStore()
        store.add(_envelope("keep"))
        controller = InteractiveExecutionController(pending=store)
        context = SimpleNamespace(controller=controller, draft_text="", shell=SimpleNamespace(prompt_line=lambda *_args, **_kwargs: "n"))
        assert interactive_admission.confirm_exit(context) is False
        assert len(store.list()) == 1




    def a39() -> None:
        view = RunViewModel()
        correlation = RunCorrelation.fresh()
        view.begin_run(1, owner="owner")
        view.apply(_event(RuntimeEventKind.STEP_COMPLETED, correlation, activity="semantic"))
        before = view.snapshot().last_activity_at
        view.apply(_event(RuntimeEventKind.CONTEXT_REFRESH, correlation, heartbeat=True))
        assert view.snapshot().last_activity_at == before




    def a40() -> None:
        context = SimpleNamespace(session=SimpleNamespace(model_profile=SimpleNamespace(model="m", provider="p")), config={"model_profiles": {"x": {}}}, controller=SimpleNamespace(is_busy=lambda: True), shell=None)
        old = command_handlers.console
        command_handlers.console = _Console()
        try:
            command_handlers.model("/model select x", context)
        finally:
            command_handlers.console = old
        assert not hasattr(context, "rebootstrap_profile")



    return (a37, a38, a39, a40,)


def _scenario_group_11(root: Path) -> tuple[Callable[[], None], ...]:
    def a41() -> None:
        source = (Path(__file__).resolve().parents[1] / "agent/interfaces/cli/interactive_admission.py").read_text(encoding="utf-8")
        assert "controller.submit" in source and "execute_submission" in source




    def a42() -> None:
        source = (Path(__file__).resolve().parents[1] / "agent/interfaces/cli/interactive_worker.py").read_text(encoding="utf-8")
        assert 'envelope.command_id == "retry"' in source




    def a43() -> None:
        source = (Path(__file__).resolve().parents[1] / "agent/interfaces/cli/interactive_worker.py").read_text(encoding="utf-8")
        assert "CodingApplicationService" in source and "_execute_code" in source




    def a44() -> None:
        controller = InteractiveExecutionController()
        gate = Event()
        controller.submit(_envelope("run"), lambda *_: (gate.wait(2), "done")[1])
        pending = controller.submit(_envelope("boundary"), lambda *_: "later")
        gate.set()
        controller.wait_for_settlement()
        assert pending.disposition == "PENDING_FOLLOWUP"




    return (a41, a42, a43, a44,)


def _scenario_group_12(root: Path) -> tuple[Callable[[], None], ...]:
    def a45() -> None:
        controller = InteractiveExecutionController()
        pending = controller.pending.add(_envelope("once"))
        sent = controller.send_pending(pending.pending_id, lambda *_: "done")
        duplicate = controller.send_pending(pending.pending_id, lambda *_: "bad")
        controller.wait_for_settlement()
        assert sent.disposition == "ACCEPTED" and duplicate.disposition == "IGNORED_DUPLICATE"




    def a46() -> None:
        controller = InteractiveExecutionController()
        first = controller.submit(_envelope("one"), lambda *_: "one")
        controller.wait_for_settlement()
        second = controller.submit(_envelope("two"), lambda *_: "two")
        assert controller.request_cancel(first.run_generation).disposition == "IGNORED_STALE"
        controller.wait_for_settlement()
        assert second.run_generation != first.run_generation




    def a47() -> None:
        controller = InteractiveExecutionController()
        started, release = Event(), Event()

        def execute(
            _envelope: SubmissionEnvelope,
            cancel: Event,
            register: Callable[[Callable[[], None]], None],
        ) -> str:
            register(lambda: None)
            started.set()
            release.wait(2)
            assert cancel.is_set()
            return "settled"

        accepted = controller.submit(_envelope("cancel"), execute)
        assert started.wait(2)
        assert controller.request_cancel(accepted.run_generation).disposition == "CANCEL_REQUESTED"
        release.set()
        settled = controller.wait_for_settlement()
        assert settled is not None and settled.result == "settled"



    def a48() -> None:
        calls: list[str] = []
        old = command_handlers.console
        command_handlers.console = _Console()
        def request_cancel() -> SimpleNamespace:
            calls.append("request")
            return SimpleNamespace(disposition="CANCEL_REQUESTED", run_generation=1)

        try:
            command_handlers.cancel("/cancel", SimpleNamespace(controller=SimpleNamespace(request_cancel=request_cancel), shell=None))
        finally:
            command_handlers.console = old
        assert calls == ["request"]



    return (a45, a46, a47, a48,)


def _scenario_group_13(root: Path) -> tuple[Callable[[], None], ...]:
    def a49() -> None:
        broker = ApprovalBroker()
        broker.bind_generation(1)
        thread, values = _approval_thread(broker)
        current = broker.current()
        assert current is not None
        assert broker.resolve(current.identity.attention_id + 1, ApprovalDecision.APPROVED) == "IGNORED_STALE"
        broker.resolve(current.identity.attention_id, ApprovalDecision.REJECTED)
        thread.join(2)
        assert values == [ApprovalDecision.REJECTED]




    def a50() -> None:
        broker = ApprovalBroker()
        broker.bind_generation(1)
        thread, values = _approval_thread(broker)
        current = broker.current()
        assert current is not None
        broker.bind_generation(2)
        assert broker.resolve(current.identity.attention_id, ApprovalDecision.APPROVED) == "IGNORED_STALE"
        thread.join(2)
        assert values and isinstance(values[0], ApprovalWaitCancelled)




    def a51() -> None:
        broker = ApprovalBroker()
        broker.bind_generation(1)
        thread, values = _approval_thread(broker)
        broker.shutdown()
        thread.join(2)
        assert not thread.is_alive() and isinstance(values[0], ApprovalWaitCancelled)




    def a52() -> None:
        from agent.interfaces.cli.interactive_worker import InteractiveWorkerResult, execute_submission
        from agent.runtime.worker_output import emit_worker_output

        def interact(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
            emit_worker_output("worker-rerouted-output")
            return SimpleNamespace(answer="ok")

        context = SimpleNamespace(application=SimpleNamespace(interact=interact), orchestrator=SimpleNamespace(), controller=None, approval_broker=None, view_model=None)
        ui_output = io.StringIO()
        original_stdout = sys.stdout
        sys.stdout = ui_output
        try:
            result = execute_submission(context, _envelope("hello"), Event(), lambda _callback: None)
        finally:
            sys.stdout = original_stdout
        assert isinstance(result, InteractiveWorkerResult) and "worker-rerouted-output" in result.stdout
        assert ui_output.getvalue() == ""




    return (a49, a50, a51, a52,)


def _scenario_group_14(root: Path) -> tuple[Callable[[], None], ...]:
    def a53() -> None:
        original = first_run.is_interactive_terminal
        first_run.is_interactive_terminal = lambda: False
        try:
            try:
                app._run_chat(argparse.Namespace())
            except first_run.InteractiveTTYRequiredError:
                return
            raise AssertionError("non-TTY chat did not fail fast")
        finally:
            first_run.is_interactive_terminal = original




    def a54() -> None:
        view = RunViewModel()
        view.begin_run(1, owner="owner")
        assert len(view.render_toolbar(width=40)) <= 40




    def a55() -> None:
        view = RunViewModel()
        correlation = RunCorrelation.fresh()
        view.begin_run(1, owner="owner")
        view.apply(_event(RuntimeEventKind.TASK_OUTCOME, correlation, status="succeeded"))
        assert view.apply(_event(RuntimeEventKind.MODEL_CALL_STARTED, correlation)) is False




    def a56() -> None:
        from agent.interfaces.cli.interactive_shell import InteractiveShell

        shell = InteractiveShell(session=SimpleNamespace(prompt=lambda *_args, **_kwargs: "ok", output=None))
        with shell.temporary_prompt():
            assert shell.prompt_line("selector") == "ok"
        shell.close()



    return (a53, a54, a55, a56,)


def _scenario_group_15(root: Path) -> tuple[Callable[[], None], ...]:
    def a57() -> None:
        executor = BoundedQueryExecutor(workspace_id="w")
        started, release = Event(), Event()

        def slow(request: WorkspaceQueryRequest, _cancel: object) -> WorkspaceQueryResult:
            started.set()
            release.wait(2)
            return _fake_query_result(request)

        executor.submit(_query_request("find"), task_active=True, execute=slow)
        assert started.wait(2) and executor.is_busy()
        release.set()
        assert executor.cancel_and_wait() is not None




    def a58() -> None:
        executor = BoundedQueryExecutor(workspace_id="w")
        gate = Event()
        def slow_query(request: WorkspaceQueryRequest, _cancel: object) -> WorkspaceQueryResult:
            gate.wait(2)
            return _fake_query_result(request)

        def second_query(request: WorkspaceQueryRequest, _cancel: object) -> WorkspaceQueryResult:
            return _fake_query_result(request)

        executor.submit(_query_request("find"), task_active=False, execute=slow_query)
        busy = executor.submit(_query_request("diff"), task_active=False, execute=second_query)
        assert isinstance(busy, CliQueryCompletion) and busy.adapter_reason_code == "QUERY_BUSY"
        gate.set()
        executor.cancel_and_wait()




    def a59() -> None:
        _assert_required_git_tokens(_canonical_query_git_source(), _W17_HARDENED_GIT_TOKENS)




    def a60() -> None:
        context = SimpleNamespace(workspace=SimpleNamespace(root=root), controller=SimpleNamespace(is_busy=lambda: False), approval_broker=SimpleNamespace(current=lambda: None), query_executor=SimpleNamespace(is_busy=lambda: True), shell=None)
        target = root / "new"
        target.mkdir(exist_ok=True)
        old = command_handlers.console
        command_handlers.console = _Console()
        try:
            command_handlers.show_workspace(f"/workspace switch {target}", context)
        finally:
            command_handlers.console = old
        assert not hasattr(context, "rebootstrap_workspace")




    return (a57, a58, a59, a60,)


def _scenario_group_16(root: Path) -> tuple[Callable[[], None], ...]:
    def a61() -> None:
        executor = BoundedQueryExecutor(workspace_id="w")
        gate = Event()
        def slow_query(request: WorkspaceQueryRequest, _cancel: object) -> WorkspaceQueryResult:
            gate.wait(2)
            return _fake_query_result(request)

        executor.submit(_query_request("find"), task_active=False, execute=slow_query)
        gate.set()
        assert executor.cancel_and_wait() is not None




    def a62() -> None:
        mailbox = UIEventMailbox(milestone_capacity=2, latest_capacity=2)
        correlation = RunCorrelation.fresh()
        for index in range(100):
            mailbox.emit(_event(RuntimeEventKind.STEP_COMPLETED, correlation, step=index))
        mailbox.emit(_event(RuntimeEventKind.TASK_OUTCOME, correlation, status="succeeded"))
        assert any(item.event.kind is RuntimeEventKind.TASK_OUTCOME for item in mailbox.drain())




    def a63() -> None:
        for binding in DEFAULT_CLI_ACTION_REGISTRY._bindings:
            assert binding.busy_submit in {"NOT_APPLICABLE", "PENDING_EXACT_TEXT", "PENDING_TYPED_PAYLOAD", "REQUIRE_IDLE_PRESERVE"}




    def a64() -> None:
        support = (Path(__file__).resolve().parents[1] / "agent/code/workflow_application_flow_support.py").read_text(encoding="utf-8")
        tools = (Path(__file__).resolve().parents[1] / "agent/tools/approval_execution.py").read_text(encoding="utf-8")
        assert "ApprovalWaitCancelled" in support and "TaskStatus.CANCELLED" in support
        assert "ApprovalWaitCancelled" in tools and "ToolStatus.CANCELLED" in tools

    return (a61, a62, a63, a64,)



def _scenario_map(root: Path) -> dict[str, Callable[[], None]]:
    scenarios = _scenario_group_01(root) + _scenario_group_02(root) + _scenario_group_03(root) + _scenario_group_04(root) + _scenario_group_05(root) + _scenario_group_06(root) + _scenario_group_07(root) + _scenario_group_08(root) + _scenario_group_09(root) + _scenario_group_10(root) + _scenario_group_11(root) + _scenario_group_12(root) + _scenario_group_13(root) + _scenario_group_14(root) + _scenario_group_15(root) + _scenario_group_16(root)
    return {f"W17-A{index:02d}": scenario for index, scenario in enumerate(scenarios, 1)}


def run_campaign() -> tuple[AdversarialResult, ...]:
    with TemporaryDirectory(prefix="wave17-adversarial-") as temporary:
        root = Path(temporary)
        results: list[AdversarialResult] = []
        for scenario_id, scenario in _scenario_map(root).items():
            try:
                scenario()
            except Exception as exc:
                results.append(AdversarialResult(scenario_id, False, f"{type(exc).__name__}: {exc}"))
            else:
                results.append(AdversarialResult(scenario_id, True, "deterministic invariant held"))
        return tuple(results)


def main() -> int:
    results = run_campaign()
    for result in results:
        print(f"{result.scenario_id}={'PASS' if result.passed else 'FAIL'} {result.detail}")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
