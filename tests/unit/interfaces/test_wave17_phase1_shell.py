from __future__ import annotations

from threading import Event, Thread
from types import SimpleNamespace

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from agent.interfaces.cli.action_registry import DEFAULT_CLI_ACTION_REGISTRY
from agent.interfaces.cli.interactive_session import _handle_ctrl_c
from agent.interfaces.cli.interactive_shell import InteractiveShell


def test_registry_completion_uses_preferred_commands_and_preserves_alias_metadata() -> None:
    assert DEFAULT_CLI_ACTION_REGISTRY.completion_items("/c") == ("/cancel", "/code")
    match = DEFAULT_CLI_ACTION_REGISTRY.match("/agente objetivo")
    assert match is not None
    assert match.used_compatibility_alias is True
    assert match.action_id == "interaction.agent_submit"
    assert match.raw_payload == "objetivo"
    assert match.binding.busy_submit == "PENDING_TYPED_PAYLOAD"


def test_prompt_session_accepts_multiline_without_mouse_capture_and_keeps_literal_background_text() -> None:
    with create_pipe_input() as pipe:
        shell = InteractiveShell(
            registry=DEFAULT_CLI_ACTION_REGISTRY,
            input=pipe,
            output=DummyOutput(),
            toolbar=lambda: "RUNNING | model ready",
        )
        result: list[str | None] = []
        thread = Thread(target=lambda: result.append(shell.prompt("> ")))
        thread.start()
        pipe.send_text("linha 1")
        pipe.send_text("\n")  # Ctrl-J inserts a newline in the W17 binding.
        pipe.send_text("linha 2")
        pipe.send_text("\r")  # Enter submits the complete draft.
        thread.join(timeout=5)
        try:
            assert not thread.is_alive()
            assert result == ["linha 1\nlinha 2"]
            shell.print_background("[bold]literal worker text[/bold]")
            assert getattr(shell.session, "mouse_support", False) is not True
        finally:
            shell.close()


def test_prompt_line_handles_eof_without_a_second_reader() -> None:
    class _Session:
        def prompt(self, *_args, **_kwargs):
            raise EOFError

    shell = InteractiveShell(session=_Session())
    assert shell.prompt_line("> ") is None


def test_background_pump_runs_while_composer_prompt_is_blocked() -> None:
    pumped = Event()
    with create_pipe_input() as pipe:
        shell = InteractiveShell(
            registry=DEFAULT_CLI_ACTION_REGISTRY,
            input=pipe,
            output=DummyOutput(),
        )
        shell.set_background_pump(pumped.set)
        result: list[str | None] = []
        thread = Thread(target=lambda: result.append(shell.prompt("> ")))
        thread.start()
        try:
            assert pumped.wait(3)
            pipe.send_text("\r")
            thread.join(timeout=3)
            assert not thread.is_alive()
            assert result == [""]
        finally:
            shell.close()


def test_background_print_does_not_wait_for_a_blocked_prompt() -> None:
    started = Event()
    release = Event()

    class _Session:
        output = DummyOutput()

        def prompt(self, *_args, **_kwargs):
            started.set()
            release.wait(3)
            return "done"

    shell = InteractiveShell(session=_Session())
    result: list[str | None] = []
    thread = Thread(target=lambda: result.append(shell.prompt("> ")))
    thread.start()
    assert started.wait(2)
    shell.print_background("background")
    release.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert result == ["done"]


def test_ctrl_c_is_contextual_escape_or_request_only_cancel() -> None:
    events: list[str] = []

    class _Shell:
        def __init__(self, draft: str = "") -> None:
            self.draft = draft

        def current_draft(self) -> str:
            return self.draft

        def clear_draft(self) -> None:
            events.append("clear")
            self.draft = ""

        def print_background(self, value: object) -> None:
            events.append(str(value))

    draft_ctx = SimpleNamespace(
        shell=_Shell("draft"),
        draft_text="draft",
        controller=SimpleNamespace(
            is_busy=lambda: True,
            request_cancel=lambda: (events.append("cancel") or SimpleNamespace(disposition="CANCEL_REQUESTED")),
        ),
        approval_broker=None,
        query_executor=None,
    )
    _handle_ctrl_c(draft_ctx)
    assert "clear" in events and "cancel" not in events

    events.clear()
    active_ctx = SimpleNamespace(
        shell=_Shell(),
        draft_text="",
        controller=SimpleNamespace(
            is_busy=lambda: True,
            request_cancel=lambda: (events.append("cancel") or SimpleNamespace(disposition="CANCEL_REQUESTED")),
        ),
        approval_broker=None,
        query_executor=None,
    )
    _handle_ctrl_c(active_ctx)
    assert events == ["cancel", "[interactive] cancel_requested; aguardando settlement"]

    events.clear()
    attention = SimpleNamespace(identity=SimpleNamespace(attention_id=4, run_generation=8))
    invalidated: list[tuple[object, object]] = []
    attention_ctx = SimpleNamespace(
        shell=_Shell(),
        draft_text="",
        controller=SimpleNamespace(is_busy=lambda: False),
        approval_broker=SimpleNamespace(
            current=lambda: attention,
            invalidate=lambda **kwargs: invalidated.append((kwargs["attention_id"], kwargs["generation"])),
        ),
        query_executor=None,
    )
    _handle_ctrl_c(attention_ctx)
    assert invalidated == [(4, 8)]

    events.clear()
    query_ctx = SimpleNamespace(
        shell=_Shell(),
        draft_text="",
        controller=SimpleNamespace(is_busy=lambda: False),
        approval_broker=SimpleNamespace(current=lambda: None),
        query_executor=SimpleNamespace(is_busy=lambda: True, request_cancel=lambda: events.append("query-cancel")),
    )
    _handle_ctrl_c(query_ctx)
    assert "query-cancel" in events
