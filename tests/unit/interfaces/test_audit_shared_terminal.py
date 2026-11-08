"""Offline regressions for shared model controls and terminal interaction."""
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from llm_agent.application.task_execution import TaskExecutionStart
from llm_agent.interactive.session import InteractiveSession
from llm_agent.interfaces.cli import app
from llm_agent.interfaces.cli.shared_terminal import SharedTerminal
from llm_agent.workspace.paths import AppPaths


@pytest.fixture
def session(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    value = InteractiveSession(TaskExecutionStart(root, AppPaths.discover(tmp_path / "home", env={}), configure_logging=False), llm_enabled=False)
    yield value
    value.close().result(timeout=5)


def test_llm_controls_are_discoverable_in_both_core_projections(session):
    commands = session.submit("/commands llm").result()[0]["answer"]
    assert "/llm" in commands
    assert any(item["command"] == "/llm" for item in session.command_catalog())
    status = session.submit("/llm status").result()[0]
    assert status["model_used"] is False
    assert "desativada" in status["answer"]
    assert session._runtime is None
    with pytest.raises(ValueError, match="NOT_CONFIGURED"):
        session.submit("/llm on")
    with pytest.raises(ValueError, match="Uso"):
        session.submit("/llm surprise")


def test_default_chat_delegates_to_shared_terminal(monkeypatch):
    monkeypatch.setattr(app.first_run, "is_interactive_terminal", lambda: True)
    seen = []
    monkeypatch.setattr(app, "_run_local", lambda args: seen.append(args) or 0)
    args = SimpleNamespace()
    assert app._run_chat(args) == 0
    assert seen == [args]


class Shell:
    def __init__(self):
        self.lines = []

    def print_background(self, value):
        self.lines.append(value)

    def print_stream(self, value):
        self.lines.append(value)


def test_pending_worker_does_not_block_prompt_and_busy_input_is_queued():
    future = Future()
    queued = []
    session = SimpleNamespace(pump_stream=lambda: None, attention=lambda: None,
                              controller=SimpleNamespace(is_busy=lambda: True),
                              enqueue=lambda value: queued.append(value) or {"pending_id": 1})
    terminal = SharedTerminal(session, Shell())
    terminal.pending.append(future)
    terminal.pump()
    assert terminal.pending == [future]
    terminal.submit("próxima tarefa")
    assert queued == ["próxima tarefa"]
    future.set_result(({"status": "succeeded", "answer": "feito"}, None))
    terminal.pump()
    assert terminal.pending == []
    assert "feito" in terminal.shell.lines


def test_attention_is_announced_once_and_controls_stay_available():
    approval = {"attention_id": 1, "run_generation": 2, "request_fingerprint": "abc",
                "action": "write", "resource": "example.txt", "prompt": "Confirmar?"}
    seen = []
    completed = Future()
    completed.set_result(({"answer": "running"}, None))
    session = SimpleNamespace(pump_stream=lambda: None, attention=lambda: approval,
                              controller=SimpleNamespace(is_busy=lambda: True),
                              submit=lambda text, **kwargs: seen.append(text) or completed)
    terminal = SharedTerminal(session, Shell())
    terminal.pump()
    terminal.pump()
    assert len(terminal.shell.lines) == 1
    terminal.submit("/status")
    assert seen == ["/status"]


def test_stream_is_not_duplicated_but_overflow_keeps_full_answer():
    session = SimpleNamespace(last_stream_truncated=False)
    terminal = SharedTerminal(session, Shell())
    result = {"status": "succeeded", "answer": "full answer"}
    terminal.render(result, streamed=True)
    assert terminal.shell.lines == []
    session.last_stream_truncated = True
    terminal.render(result, streamed=True)
    assert "full answer" in terminal.shell.lines
