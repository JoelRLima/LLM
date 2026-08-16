from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.interfaces.cli import command_handlers
from agent.interfaces.cli.commands import EXACT_HANDLERS, PREFIX_HANDLERS, handle_command
from agent.skills import load_tool_registry
from agent.tools.authority import OperationalMode, operational_mode_capabilities
from agent.tools.contracts import ToolResult, ToolStatus
from agent.tools.invocation_gateway import ToolInvocationGateway


class _Console:
    def __init__(self) -> None:
        self.output: list[str] = []

    def print(self, *values: object, **_kwargs: object) -> None:
        self.output.append(" ".join(map(str, values)))


def _context(tmp_path: Path) -> SimpleNamespace:
    registry = load_tool_registry(base_dir=tmp_path)
    gateway = ToolInvocationGateway(registry)
    capabilities = operational_mode_capabilities(OperationalMode.READ_ONLY)
    gateway.set_capability_ceiling(capabilities, mode=OperationalMode.READ_ONLY.display_name)
    return SimpleNamespace(
        orchestrator=SimpleNamespace(
            tool_invocation_gateway=gateway,
            # A fresh session has no persona/planning projection yet.
            active_skills=[],
            allowed_capabilities=capabilities,
        ),
        workspace=SimpleNamespace(root=tmp_path),
    )


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/read controle.txt", "original"),
        ("/list", "controle.txt"),
        ("/find marker", "marker"),
    ],
)
def test_fresh_explicit_observation_commands_ignore_empty_planning_projection(
    command: str,
    expected: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "controle.txt").write_text("original\nmarker", encoding="utf-8")
    output = _Console()
    monkeypatch.setattr(command_handlers, "console", output)

    handled, should_exit = handle_command(command, _context(tmp_path))

    assert handled is True and should_exit is False
    rendered = "\n".join(output.output)
    assert expected in rendered
    assert "visibilidade de planning" not in rendered


def test_explicit_helper_does_not_forward_planner_visibility(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Gateway:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] | None = None

        def run(self, _name: str, _args: dict[str, object], **kwargs: object) -> ToolResult:
            self.kwargs = kwargs
            return ToolResult("invocation", ToolStatus.SUCCEEDED, data="ok")

    gateway = _Gateway()
    output = _Console()
    monkeypatch.setattr(command_handlers, "console", output)
    context = SimpleNamespace(
        orchestrator=SimpleNamespace(
            tool_invocation_gateway=gateway,
            active_skills=[],
            allowed_capabilities=frozenset({"read"}),
        )
    )

    command_handlers.read_file("/read controle.txt", context)

    assert gateway.kwargs is not None
    assert gateway.kwargs["active_skills"] is None


def test_search_keeps_network_policy_without_planning_visibility_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _Console()
    monkeypatch.setattr(command_handlers, "console", output)

    command_handlers.web_search("/search example", _context(tmp_path))

    rendered = "\n".join(output.output)
    assert "visibilidade de planning" not in rendered
    assert "Capabilities nao autorizadas" in rendered or "network" in rendered.lower()


def test_planner_empty_projection_remains_a_gateway_denylist(tmp_path: Path) -> None:
    registry = load_tool_registry(base_dir=tmp_path)
    result = ToolInvocationGateway(registry).run(
        "file_reader", {"file_path": "controle.txt"}, active_skills=[]
    )

    assert result.status is ToolStatus.PERMISSION_DENIED
    assert result.error is not None
    assert result.error.code == "PERMISSION_DENIED"


def test_explicit_command_surface_has_no_arbitrary_tool_dispatch() -> None:
    names = set(EXACT_HANDLERS) | {prefix for prefix, _handler in PREFIX_HANDLERS}

    assert {"/read", "/list", "/ls", "/find", "/search"} <= names
    assert "/tool" not in names
