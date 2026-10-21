from __future__ import annotations

from types import SimpleNamespace

import pytest

from llm_agent.interfaces.cli import interactive_commands


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        conversation=object(),
        config={"model_profiles": {"one": {"model": "m1"}}},
        app_paths=object(),
        config_path="custom.json",
        controller=None,
        prompt_line=None,
        rebootstrap_profile=None,
    )


def test_model_selection_preserves_interface_guard_and_rebootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _context()
    output: list[object] = []
    selected: list[tuple[object, ...]] = []
    monkeypatch.setattr(interactive_commands, "read_conversation", lambda _conversation: SimpleNamespace(model="m", provider="p"))
    monkeypatch.setattr(interactive_commands, "_ui_print", lambda _ctx, value: output.append(value))
    monkeypatch.setattr(
        "llm_agent.application.model_profile_selection.select_default_model_profile",
        lambda *args, **kwargs: selected.append((*args, kwargs)),
    )

    interactive_commands.model("/model select one", ctx)

    assert selected == [(ctx.config, "one", ctx.app_paths, {"config_path": "custom.json"})]
    assert ctx.rebootstrap_profile == "one"
    assert output == ["model: profile one selected; recreating the session"]


def test_model_selection_unknown_or_busy_does_not_call_application(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _context()
    output: list[object] = []
    calls: list[object] = []
    monkeypatch.setattr(interactive_commands, "read_conversation", lambda _conversation: SimpleNamespace(model="m", provider="p"))
    monkeypatch.setattr(interactive_commands, "_ui_print", lambda _ctx, value: output.append(value))
    monkeypatch.setattr("llm_agent.application.model_profile_selection.select_default_model_profile", lambda *_args, **_kwargs: calls.append(True))

    interactive_commands.model("/model select missing", ctx)
    assert output[-1] == "model: profile desconhecido: missing"
    ctx.controller = SimpleNamespace(is_busy=lambda: True)
    interactive_commands.model("/model select one", ctx)
    assert output[-1] == "model: selection requires an idle session"
    assert calls == []
    assert ctx.rebootstrap_profile is None


def test_model_selection_cancel_does_not_call_application(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _context()
    ctx.prompt_line = lambda *_args, **_kwargs: ""
    calls: list[object] = []
    monkeypatch.setattr(interactive_commands, "read_conversation", lambda _conversation: SimpleNamespace(model="m", provider="p"))
    monkeypatch.setattr(interactive_commands, "_ui_print", lambda *_args: None)

    class Selector:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def choose(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(cancelled=True, item_id=None)

    monkeypatch.setattr("llm_agent.interfaces.cli.selector.TerminalSelector", Selector)
    monkeypatch.setattr("llm_agent.application.model_profile_selection.select_default_model_profile", lambda *_args, **_kwargs: calls.append(True))
    interactive_commands.model("/model select", ctx)

    assert calls == []
    assert ctx.rebootstrap_profile is None


def test_model_selection_failure_propagates_without_rebootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _context()
    monkeypatch.setattr(interactive_commands, "read_conversation", lambda _conversation: SimpleNamespace(model="m", provider="p"))
    monkeypatch.setattr(interactive_commands, "_ui_print", lambda *_args: None)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("write failed")

    monkeypatch.setattr("llm_agent.application.model_profile_selection.select_default_model_profile", fail)
    with pytest.raises(OSError, match="write failed"):
        interactive_commands.model("/model select one", ctx)
    assert ctx.rebootstrap_profile is None
