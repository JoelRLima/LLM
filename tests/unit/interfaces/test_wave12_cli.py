from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.interfaces.cli import app as cli
from agent.interfaces.cli import commands


@dataclass
class _Result:
    success: bool = True
    answer: str = "ok"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"success": self.success, "status": "succeeded", "answer": self.answer, "error": self.error}


class _InteractionApplication:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.closed = 0

    def interact(self, text: str, **kwargs: Any) -> _Result:
        self.calls.append({"text": text, **kwargs})
        return _Result()

    def close(self) -> None:
        self.closed += 1


def test_headless_normal_run_uses_task_interaction_boundary(monkeypatch) -> None:
    application = _InteractionApplication()
    monkeypatch.setattr(cli, "_create_application", lambda *_args, **_kwargs: application)
    assert cli.main(["run", "--json", "/read", "Analyze", "parser.py"]) == 0
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
    context = type("Context", (), {"application": application})()
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
