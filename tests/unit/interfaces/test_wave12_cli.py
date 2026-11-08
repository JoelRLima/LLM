from __future__ import annotations

from typing import Any

from llm_agent.agent.interaction.types import AgentInteractionResult
from llm_agent.application.task_execution import _retain_runtime
from llm_agent.interfaces.cli import app as cli
from llm_agent.interfaces.cli import commands


class _InteractionApplication:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.closed = 0

    def interact(self, text: str, **kwargs: Any) -> AgentInteractionResult:
        self.calls.append({"text": text, **kwargs})
        return AgentInteractionResult("succeeded", "ok", None)

    def close(self) -> None:
        self.closed += 1


def test_headless_normal_run_uses_task_interaction_boundary(monkeypatch) -> None:
    application = _InteractionApplication()
    monkeypatch.setattr(cli, "_create_application", lambda *_args, **_kwargs: _retain_runtime(application))
    assert cli.main(["run", "--workspace", "workspace", "--json", "/read", "Analyze", "parser.py"]) == 0
    assert application.closed == 1
    assert application.calls == [
        {
            "text": "Analyze parser.py",
            "boundary": "task",
            "visible_user_text": "/read Analyze parser.py",
            "task_payload": "/read Analyze parser.py",
        }
    ]


def test_agent_command_with_payload_uses_unified_task_boundary() -> None:
    application = _InteractionApplication()
    context = type("Context", (), {"task_execution": _retain_runtime(application)})()
    commands.handle_command("/agent /read Analyze parser.py", context)
    assert application.calls == [
        {
            "text": "/read Analyze parser.py",
            "boundary": "task",
            "visible_user_text": "/agent /read Analyze parser.py",
            "task_payload": "/read Analyze parser.py",
        }
    ]


def test_agent_without_payload_only_shows_unified_status_not_a_mode_toggle(capsys) -> None:
    context = type("Context", (), {"application": _InteractionApplication(), "modo_agente": True})()
    commands.handle_command("/agent", context)
    assert context.modo_agente is True
    assert "unificado" in capsys.readouterr().out.casefold()
