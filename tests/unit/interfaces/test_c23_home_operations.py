from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("operation_fails", [False, True])
def test_engineering_approval_precedes_startup_guard_and_guard_cleans_up(monkeypatch: pytest.MonkeyPatch, operation_fails: bool) -> None:
    import llm_agent.interfaces.cli.engineering as cli

    events: list[str] = []

    @contextmanager
    def guard(_paths):
        events.append("guard enter")
        try:
            yield None
        finally:
            events.append("guard close")

    monkeypatch.setattr(cli.AppPaths, "discover", lambda **_kwargs: SimpleNamespace(home_dir=Path("home")))
    monkeypatch.setattr(cli, "discover_source_repository_context", lambda: None)
    monkeypatch.setattr(cli, "_collect_interactive_permissions", lambda *_args: (events.append("approval") or (False, False, None)))
    monkeypatch.setattr(cli, "startup_home_operation", guard)

    def run_engineering(*_args, run_guard, **_kwargs):
        def operation():
            events.append("operation")
            if operation_fails:
                raise RuntimeError("engineering operation failed")
            return SimpleNamespace(payload={}, exit_code=0)

        return SimpleNamespace(payload=run_guard(operation).payload, exit_code=0)

    monkeypatch.setattr(cli, "run_engineering", run_engineering)
    args = SimpleNamespace(
        home=None, test_command=None, workspace=None, operation_id="engineering.test", params_json=None,
        fault_json=None, json_output=True, allow_external=False, allow_network=False,
    )
    if operation_fails:
        with pytest.raises(RuntimeError, match="engineering operation failed"):
            cli.run_engineering_cli(args, print_json=lambda _payload: None)
    else:
        assert cli.run_engineering_cli(args, print_json=lambda _payload: None) == 0
    assert events == ["approval", "guard enter", "operation", "guard close"]


def test_extensions_only_guards_existing_mutation_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    import llm_agent.interfaces.cli.extensions as cli

    events: list[str] = []

    @contextmanager
    def guard(_paths):
        events.append("guard enter")
        try:
            yield None
        finally:
            events.append("guard close")

    monkeypatch.setattr(cli, "transient_home_operation", guard)
    monkeypatch.setattr(cli, "_run_extensions", lambda args, **_kwargs: (events.append(args.extensions_command) or 0))
    paths = SimpleNamespace(home_dir=Path("home"))
    common = {"app_paths": paths, "workspace": Path("workspace")}
    assert cli.run_extensions(SimpleNamespace(extensions_command="grant"), **common) == 0
    assert events == ["guard enter", "grant", "guard close"]
    events.clear()
    assert cli.run_extensions(SimpleNamespace(extensions_command="list"), **common) == 0
    assert events == ["list"]


def test_first_run_guided_setup_stays_inside_transient_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    import llm_agent.application.home_operations as home_operations
    import llm_agent.interfaces.cli.first_run as cli

    events: list[str] = []

    @contextmanager
    def guard(_paths):
        events.append("guard enter")
        try:
            yield None
        finally:
            events.append("guard close")

    class Console:
        def print(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(cli, "configuration_path", lambda *_args: Path("configuration"))
    monkeypatch.setattr(cli, "initialize_configuration", lambda *_args: (events.append("initialize") or Path("configuration")))
    monkeypatch.setattr(home_operations, "transient_home_operation", guard)
    monkeypatch.setattr(cli, "read_first_run_configuration", lambda *_args: (events.append("read") or object()))
    monkeypatch.setattr(cli, "_complete_guided_setup", lambda *_args: events.append("setup"))
    result = cli.recover_first_run_config(
        SimpleNamespace(home=None, config=None), console=Console(), app_paths=SimpleNamespace(home_dir=Path("home")),
        prompt=lambda _message: (events.append("prompt") or "yes"),
    )
    assert result == 0
    assert events == ["prompt", "initialize", "guard enter", "read", "setup", "guard close"]


def test_engineering_mcp_closes_startup_guard_when_server_unwinds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    import llm_agent.interfaces.mcp.engineering_server as server

    events: list[str] = []

    @contextmanager
    def guard(_paths):
        events.append("guard enter")
        try:
            yield None
        finally:
            events.append("guard close")

    binding = SimpleNamespace(root=tmp_path.resolve(), workspace_id="workspace-c23")
    monkeypatch.setattr(server.MCPWorkspaceBinding, "from_path", classmethod(lambda _cls, _value: binding))
    monkeypatch.setattr(server.AppPaths, "discover", classmethod(lambda _cls, app_home=None: SimpleNamespace(home_dir=tmp_path)))
    monkeypatch.setattr(server, "startup_home_operation", guard)
    monkeypatch.setattr(server, "_default_adapter", lambda *_args: object())

    def interrupted(*_args):
        events.append("server")
        raise KeyboardInterrupt

    monkeypatch.setattr(server.anyio, "run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        server.serve_engineering_stdio(tmp_path)
    assert events == ["guard enter", "server", "guard close"]
