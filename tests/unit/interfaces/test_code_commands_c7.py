"""C7 command boundary parity and ordering."""

from __future__ import annotations

from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest

from llm_agent.agent.code.changes import ChangePreview
from llm_agent.agent.code.commands import CODE_COMMAND_HELP
from llm_agent.agent.code.policy import ProposalAssessment
from llm_agent.agent.runtime.context import Artifact, TaskResult, TaskStatus
from llm_agent.application import code_commands
from llm_agent.application.conversation import bind_conversation
from llm_agent.application.task_execution import _retain_runtime
from llm_agent.interfaces.cli import command_handlers, interactive_worker


def _run(text: str, **overrides: Any) -> code_commands.CodeCommandOutcome:
    options: dict[str, Any] = {
        "config": {}, "conversation": bind_conversation(_retain_runtime(SimpleNamespace(session=SimpleNamespace(gateway=None)))), "workspace_root": ".",
        "allows_write_validate": lambda: True, "is_full_mode": lambda: True,
        "approval_factory": lambda _yes: None,
    }
    options.update(overrides)
    return code_commands.execute_code_command(text, **options)


def test_help_parse_errors_and_unknown_action_keep_agent_text() -> None:
    for command in ("/code", "/CODE ajuda", "/code help"):
        outcome = _run(command, allows_write_validate=lambda: pytest.fail("help reached mode"))
        assert (outcome.kind, outcome.status, outcome.help_text) == (
            "help", "succeeded", CODE_COMMAND_HELP,
        )
    assert _run("not-code").error == "Comando deve começar com /code."
    unknown = _run("/code future_action")
    assert unknown.kind == "parse_error"
    assert unknown.error == f"Ação desconhecida: future_action.\n{CODE_COMMAND_HELP}"
    assert unknown.status == "failed" and unknown.summary == unknown.error


def test_sync_help_keeps_panel_and_full_text(monkeypatch: pytest.MonkeyPatch) -> None:
    panels: list[object] = []
    monkeypatch.setattr(command_handlers.console, "print", lambda value, **_kwargs: panels.append(value))
    command_handlers.code_command("/code ajuda", _worker_context(None))
    assert len(panels) == 1
    assert panels[0].renderable == CODE_COMMAND_HELP
    assert panels[0].title == "[bold blue]/code[/bold blue]"


@pytest.mark.parametrize("command", [
    "/code modify a.py -- change", "/code template parallel_analyze a.py",
])
def test_mode_denial_precedes_context_even_for_read_template(
    monkeypatch: pytest.MonkeyPatch, command: str,
) -> None:
    monkeypatch.setattr(code_commands, "build_code_context", lambda *_: pytest.fail("context built"))
    outcome = _run(command, allows_write_validate=lambda: False)
    assert outcome.kind == "mode_denied" and outcome.status == "blocked"
    assert outcome.error == "Ação negada pelo modo operacional ativo."


def test_tests_denial_precedes_context_and_unrequested_tests_skip_full_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(code_commands, "build_code_context", lambda *_: pytest.fail("context built"))
    outcome = _run("/code modify a.py --tests -- change", is_full_mode=lambda: False)
    assert outcome.kind == "tests_denied"
    assert outcome.error == "Execução de testes exige modo FULL."
    outcome = _run("/code modify a.py -- change", is_full_mode=lambda: pytest.fail("FULL queried"),
                   allows_write_validate=lambda: False)
    assert outcome.kind == "mode_denied"


def test_order_projection_and_internal_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    events: list[str] = []
    cancelled: list[str] = []
    context = SimpleNamespace(cancellation=SimpleNamespace(cancel=lambda: cancelled.append("cancel")))
    snapshot: dict[str, Any] = {"model": "test"}
    gateway_handle = object()

    def build(config: Any, gateway: Any) -> Any:
        assert config is snapshot and gateway is gateway_handle
        events.append("context")
        return context

    class Service:
        def __init__(self, root: Any, received_context: Any, config: Any) -> None:
            assert Path(root) == tmp_path and received_context is context and config is snapshot
            events.append("service_init")

        def execute(self, request: Any, approver: Any) -> TaskResult:
            events.append("execute")
            assert (request.action, request.objective, request.targets, request.include_tests, request.graph) == (
                "modify", "change", ("a.py",), True, None,
            )
            assert approver.requires_explicit_approval is True
            assert approver.approve(ChangePreview("id", ("a.py",), "diff"), ProposalAssessment(0.8, True)) is True
            return TaskResult(
                TaskStatus.SUCCEEDED, summary="done",
                artifacts=(Artifact("changeset", content="diff"),),
                diagnostics=({"code": "X", "file_path": "a.py", "line": 3, "message": "notice"},),
            )

    monkeypatch.setattr(code_commands, "build_code_context", build)
    monkeypatch.setattr(code_commands, "CodingApplicationService", Service)
    outcome = _run(
        "/code modify a.py --tests --yes -- change", config=snapshot,
        conversation=bind_conversation(_retain_runtime(SimpleNamespace(session=SimpleNamespace(gateway=gateway_handle)))), workspace_root=tmp_path,
        allows_write_validate=lambda: events.append("mode") or True,
        is_full_mode=lambda: events.append("full") or True,
        register_cancellation=lambda _callback: events.append("register"),
        cancellation_requested=lambda: events.append("requested") or True,
        approval_factory=lambda yes: events.append("approval_factory") or (
            lambda _preview, _assessment: yes
        ),
    )
    assert events == ["mode", "full", "context", "register", "requested", "service_init", "approval_factory", "execute"]
    assert cancelled == ["cancel"]
    assert (outcome.kind, outcome.status, outcome.summary, outcome.error) == ("executed", "succeeded", "done", None)
    assert outcome.artifacts == (("changeset", "diff"),)
    assert outcome.diagnostics == (("X", "a.py", "3", "notice"),)


@pytest.mark.parametrize("action", ["analyze", "review"])
def test_read_actions_bypass_mutation_gates(monkeypatch: pytest.MonkeyPatch, action: str) -> None:
    requests: list[Any] = []
    monkeypatch.setattr(code_commands, "build_code_context", lambda *_: SimpleNamespace(cancellation=SimpleNamespace(cancel=lambda: None)))

    class Service:
        def __init__(self, *_args: Any) -> None:
            pass

        def execute(self, request: Any, approver: Any) -> TaskResult:
            del approver
            requests.append(request)
            return TaskResult(TaskStatus.SUCCEEDED, summary=action)

    monkeypatch.setattr(code_commands, "CodingApplicationService", Service)
    outcome = _run(f"/code {action} a.py", allows_write_validate=lambda: pytest.fail("mode gate"))
    assert outcome.status == "succeeded" and requests[0].action == action


def test_unknown_template_reaches_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(code_commands, "build_code_context", lambda *_: SimpleNamespace(cancellation=SimpleNamespace(cancel=lambda: None)))

    class Service:
        def __init__(self, *_args: Any) -> None:
            pass

        def execute(self, request: Any, approver: Any) -> TaskResult:
            del approver
            assert request.template == "unknown"
            raise ValueError("Template de código desconhecido: unknown")

    monkeypatch.setattr(code_commands, "CodingApplicationService", Service)
    with pytest.raises(ValueError, match="Template de código desconhecido"):
        _run("/code template unknown a.py")


def test_mutation_without_tests_does_not_query_full(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(code_commands, "build_code_context", lambda *_: SimpleNamespace(cancellation=SimpleNamespace(cancel=lambda: None)))

    class Service:
        def __init__(self, *_args: Any) -> None:
            pass

        def execute(self, request: Any, approver: Any) -> TaskResult:
            assert request.include_tests is False
            assert approver is None
            return TaskResult(TaskStatus.SUCCEEDED)

    monkeypatch.setattr(code_commands, "CodingApplicationService", Service)
    outcome = _run("/code modify a.py -- change", is_full_mode=lambda: pytest.fail("FULL queried"))
    assert outcome.status == "succeeded"


def _worker_context(broker: Any) -> Any:
    return SimpleNamespace(
        task_execution=_retain_runtime(SimpleNamespace(orchestrator=SimpleNamespace(mode_allows=lambda _caps: True, operational_mode=None))),
        conversation=bind_conversation(_retain_runtime(SimpleNamespace(session=SimpleNamespace(gateway=None)))), config={},
        workspace=SimpleNamespace(root="."), approval_broker=broker,
    )


def test_worker_yes_still_calls_broker_and_absent_broker_passes_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(code_commands, "build_code_context", lambda *_: SimpleNamespace(cancellation=SimpleNamespace(cancel=lambda: None)))
    approvals: list[str] = []
    received: list[Any] = []

    class Service:
        def __init__(self, *_args: Any) -> None:
            pass

        def execute(self, request: Any, approver: Any) -> TaskResult:
            del request
            received.append(approver)
            if approver is not None:
                assert approver.requires_explicit_approval is True
                assert approver.approve(ChangePreview("id", ("a.py",), "diff"), ProposalAssessment(0.8, True)) is False
            return TaskResult(TaskStatus.CANCELLED, error="approval_rejected")

    monkeypatch.setattr(code_commands, "CodingApplicationService", Service)
    broker = SimpleNamespace(approve_change=lambda *_: approvals.append("broker") or False)
    envelope = SimpleNamespace(visible_text="/code modify a.py --yes -- change")
    result = interactive_worker._execute_code(_worker_context(broker), envelope, Event(), lambda _cb: None)
    assert result.status == "cancelled" and approvals == ["broker"]
    interactive_worker._execute_code(_worker_context(None), envelope, Event(), lambda _cb: None)
    assert received[1] is None


def test_worker_registers_then_applies_existing_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    def cancel() -> None:
        events.append("cancel")
    monkeypatch.setattr(
        code_commands, "build_code_context",
        lambda *_: events.append("context") or SimpleNamespace(cancellation=SimpleNamespace(cancel=cancel)),
    )

    class Service:
        def __init__(self, *_args: Any) -> None:
            events.append("service_init")

        def execute(self, request: Any, approver: Any) -> TaskResult:
            del request, approver
            events.append("execute")
            return TaskResult(TaskStatus.CANCELLED)

    monkeypatch.setattr(code_commands, "CodingApplicationService", Service)
    event = Event()
    event.set()

    def register(callback: Any) -> None:
        assert callback is cancel
        events.append("register")

    outcome = interactive_worker._execute_code(
        _worker_context(None), SimpleNamespace(visible_text="/code analyze a.py"), event, register,
    )
    assert outcome.status == "cancelled"
    assert events == ["context", "register", "cancel", "service_init", "execute"]


def test_worker_help_fallback_and_sync_yes_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    help_result = interactive_worker._execute_code(
        _worker_context(None), SimpleNamespace(visible_text="/code help"), Event(), lambda _cb: None
    )
    assert (help_result.status, help_result.answer, help_result.summary) == (
        "succeeded", "/code help", "/code help",
    )
    approvals: list[bool] = []

    class Approver:
        def __init__(self, assume_yes: bool) -> None:
            approvals.append(assume_yes)

        def approve(self, _preview: object, _assessment: object) -> bool:
            return approvals[-1]

    monkeypatch.setattr(command_handlers, "ConsoleChangeApprover", Approver)
    monkeypatch.setattr(code_commands, "build_code_context", lambda *_: SimpleNamespace(cancellation=SimpleNamespace(cancel=lambda: None)))

    class Service:
        def __init__(self, *_args: Any) -> None:
            pass

        def execute(self, request: Any, approver: Any) -> TaskResult:
            del request
            assert approver.approve(ChangePreview("id", ("a.py",), "diff"), ProposalAssessment(0.8, True)) is approvals[-1]
            return TaskResult(TaskStatus.SUCCEEDED, summary="done")

    monkeypatch.setattr(code_commands, "CodingApplicationService", Service)
    monkeypatch.setattr(command_handlers, "render_code_result", lambda _outcome: None)
    ctx = _worker_context(None)
    command_handlers.code_command("/code modify a.py --yes -- change", ctx)
    command_handlers.code_command("/code modify a.py -- change", ctx)
    assert approvals == [True, False]
