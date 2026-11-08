from __future__ import annotations

import pytest

from llm_agent.interactive import local_commands


def test_local_commands_forward_to_application_owners(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, conversation = object(), object()
    calls: list[tuple[object, ...]] = []
    view = object()
    monkeypatch.setattr(local_commands, "read_conversation", lambda got: calls.append(("read", got)) or view)
    monkeypatch.setattr(local_commands, "configure_conversation", lambda got, **kw: calls.append(("configure", got, kw)))
    monkeypatch.setattr(local_commands, "execute_history_command", lambda *args: calls.append(("history", *args)) or "history-result")
    monkeypatch.setattr(local_commands, "execute_memory_command", lambda *args, **kw: calls.append(("memory", *args, kw)) or {"ok": True})
    monkeypatch.setattr(local_commands, "select_execution_mode", lambda *args: calls.append(("mode", *args)) or "selected")
    monkeypatch.setattr(local_commands, "read_execution_capabilities", lambda *args: "capabilities")
    monkeypatch.setattr(local_commands, "execute_workspace_command", lambda *args: calls.append(("workspace", *args)) or {"ok": True})
    service = local_commands.LocalCommandService(runtime, conversation)

    assert service.read_conversation() is view
    assert service.set_system_prompt("system") is view
    assert service.set_thinking_budget(321) is view
    assert service.history("save", "history.json") == "history-result"
    assert service.memory("remember", key="k", value="v")["ok"]
    assert service.execution_mode() == "capabilities"
    assert service.execution_mode("editor") == "selected"
    assert service.workspace_command("find", "needle")["ok"]
    assert ("configure", conversation, {"system_prompt": "system"}) in calls
    assert ("configure", conversation, {"thinking_budget": 321}) in calls
    assert ("workspace", runtime, "find", "needle") in calls


def test_diagnostic_cycle_is_service_owned(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[object, ...]] = []
    monkeypatch.setattr(local_commands, "apply_interactive_diagnostic_mode", lambda *a, **kw: seen.append((a, kw)))
    service = local_commands.LocalCommandService(object(), object())

    assert [service.diagnostics() for _ in range(4)] == [1, 2, 0, 1]
    assert [row[0][1] for row in seen] == [1, 2, 0, 1]
    assert service.diagnostics(2) == 2
    with pytest.raises(ValueError):
        service.diagnostics(3)
    with pytest.raises(ValueError):
        service.set_thinking_budget(-1)
