from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.interfaces.cli import app as cli
from agent.interfaces.cli import command_handlers, workspace_entry
from agent.interfaces.cli.commands import handle_command
from agent.interfaces.cli.workspace_entry import (
    canonical_workspace,
    choose_workspace,
    load_last_workspace,
    remember_workspace,
)
from agent.runtime.config_errors import ConfigNotFound
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.paths import AppPaths


class _Console:
    def __init__(self, *answers: str) -> None:
        self.answers = iter(answers)
        self.output: list[str] = []

    def print(self, *values: object, **_: object) -> None:
        self.output.append(" ".join(map(str, values)))

    def input(self, _: str) -> str:
        return next(self.answers)


def test_canonical_workspace_rejects_missing_and_files(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        canonical_workspace(tmp_path / "missing")
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        canonical_workspace(file_path)


def test_choose_workspace_current_directory_is_canonical(tmp_path: Path) -> None:
    console = _Console("1")

    selected = choose_workspace(console=console, current=tmp_path / ".")

    assert selected == tmp_path.resolve()
    assert "Workspace:" in "\n".join(console.output)


def test_choose_workspace_can_reopen_last_workspace_with_spaces_and_unicode(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    last = tmp_path / "Pasta com espaços – projeto"
    current.mkdir()
    last.mkdir()
    console = _Console("1")

    selected = choose_workspace(console=console, current=current, last_workspace=last)

    assert selected == last.resolve()
    rendered = "\n".join(console.output)
    assert "Reabrir último diretório" in rendered
    assert str(last.resolve()) in rendered


def test_last_workspace_state_round_trip_and_stale_state_fails_closed(
    tmp_path: Path,
) -> None:
    app_paths = AppPaths.discover(app_home=tmp_path / "app")
    last = tmp_path / "last"
    last.mkdir()

    remember_workspace(app_paths, last)

    assert load_last_workspace(app_paths) == last.resolve()
    app_paths.last_workspace_file.write_text(
        '{"schema_version": 1, "workspace": "missing"}\n',
        encoding="utf-8",
    )
    assert load_last_workspace(app_paths) is None


def test_stale_last_workspace_does_not_remove_normal_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    monkeypatch.setattr(workspace_entry, "native_picker_available", lambda: False)
    console = _Console("1")

    selected = choose_workspace(
        console=console,
        current=current,
        last_workspace=tmp_path / "gone",
    )

    assert selected == current.resolve()
    assert "Reabrir último diretório" not in "\n".join(console.output)


def test_choose_workspace_reprompts_invalid_path_then_accepts_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected_root = tmp_path / "selected"
    selected_root.mkdir()
    console = _Console("2", str(tmp_path / "missing"), "2", str(selected_root))

    # Non-Windows fallback keeps the manual path as option 2.
    monkeypatch.setattr(workspace_entry, "native_picker_available", lambda: False)

    selected = choose_workspace(console=console, current=tmp_path)

    assert selected == selected_root.resolve()
    assert any("Workspace inválido" in item for item in console.output)
    assert not (tmp_path / "missing").exists()


def test_choose_workspace_native_picker_returns_canonical_path_with_spaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected_root = tmp_path / "Pasta com espaços – projeto"
    selected_root.mkdir()
    monkeypatch.setattr(workspace_entry, "native_picker_available", lambda: True)
    monkeypatch.setattr(workspace_entry, "choose_directory_native", lambda: selected_root)
    console = _Console("2")

    selected = choose_workspace(console=console, current=tmp_path)

    assert selected == selected_root.resolve()
    assert any("Procurar pasta" in item for item in console.output)


def test_choose_workspace_native_cancel_returns_to_chooser_then_manual(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected_root = tmp_path / "selected"
    selected_root.mkdir()
    monkeypatch.setattr(workspace_entry, "native_picker_available", lambda: True)
    monkeypatch.setattr(workspace_entry, "choose_directory_native", lambda: None)
    console = _Console("2", "3", str(selected_root))

    selected = choose_workspace(console=console, current=tmp_path)

    assert selected == selected_root.resolve()
    assert any("Nenhuma pasta selecionada" in item for item in console.output)


def test_choose_workspace_native_failure_falls_back_to_manual(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected_root = tmp_path / "selected"
    selected_root.mkdir()
    monkeypatch.setattr(workspace_entry, "native_picker_available", lambda: True)
    monkeypatch.setattr(
        workspace_entry,
        "choose_directory_native",
        lambda: (_ for _ in ()).throw(workspace_entry.NativePickerUnavailable("picker failed")),
    )
    console = _Console("2", "3", str(selected_root))

    selected = choose_workspace(console=console, current=tmp_path)

    assert selected == selected_root.resolve()
    assert any("picker failed" in item for item in console.output)


def test_choose_workspace_native_invalid_result_reprompts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing"
    selected_root = tmp_path / "selected"
    selected_root.mkdir()
    monkeypatch.setattr(workspace_entry, "native_picker_available", lambda: True)
    monkeypatch.setattr(workspace_entry, "choose_directory_native", lambda: missing)
    console = _Console("2", "3", str(selected_root))

    selected = choose_workspace(console=console, current=tmp_path)

    assert selected == selected_root.resolve()
    assert any("Workspace inválido" in item for item in console.output)


def test_chat_tty_without_override_passes_selected_workspace_to_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_home = tmp_path / "app-home"
    ConfigRepository(AppPaths.discover(app_home=app_home)).initialize()
    canonical_root = tmp_path / "canonical-root"
    canonical_root.mkdir()
    application = SimpleNamespace(
        session=SimpleNamespace(config={}),
        orchestrator=SimpleNamespace(),
        config={},
        paths=SimpleNamespace(),
        workspace=SimpleNamespace(root=canonical_root.resolve()),
        workspace_paths=SimpleNamespace(),
        closed=False,
        close=lambda: None,
    )
    selected = tmp_path / "selected"
    selected.mkdir()
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli.first_run, "is_interactive_terminal", lambda: True)
    output = _Console("2", str(selected))
    monkeypatch.setattr(cli, "console", output)
    monkeypatch.setattr(workspace_entry, "native_picker_available", lambda: False)
    def create(args: object, **_: object) -> object:
        seen["args"] = args
        return application

    monkeypatch.setattr(cli, "_create_application", create)
    monkeypatch.setattr(cli, "_chat_loop", lambda _context: None)

    assert cli.main(["chat", "--home", str(app_home)]) == 0

    assert Path(seen["args"].workspace).resolve() == selected.resolve()
    rendered = "\n".join(output.output)
    assert rendered.count("Workspace ativo") == 1
    assert str(canonical_root.resolve()) in rendered


def test_chat_tty_reopens_persisted_last_workspace_and_records_active_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_home = tmp_path / "app-home"
    app_paths = AppPaths.discover(app_home=app_home)
    ConfigRepository(app_paths).initialize()
    last = tmp_path / "Pasta com espaços – projeto"
    last.mkdir()
    remember_workspace(app_paths, last)
    application = SimpleNamespace(
        session=SimpleNamespace(config={}),
        orchestrator=SimpleNamespace(),
        config={},
        paths=app_paths,
        workspace=SimpleNamespace(root=last.resolve()),
        workspace_paths=SimpleNamespace(),
        close=lambda: None,
    )
    monkeypatch.setattr(cli.first_run, "is_interactive_terminal", lambda: True)
    output = _Console("1")
    monkeypatch.setattr(cli, "console", output)
    monkeypatch.setattr(cli, "_create_application", lambda *args, **kwargs: application)
    monkeypatch.setattr(cli, "_chat_loop", lambda _context: None)
    monkeypatch.setattr(workspace_entry, "native_picker_available", lambda: False)

    assert cli.main(["chat", "--home", str(app_home)]) == 0

    rendered = "\n".join(output.output)
    assert "Reabrir último diretório" in rendered
    assert Path(application.workspace.root) == last.resolve()
    assert load_last_workspace(app_paths) == last.resolve()


def test_chat_explicit_workspace_bypasses_chooser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    application = SimpleNamespace(
        session=SimpleNamespace(config={}), orchestrator=SimpleNamespace(), config={},
        paths=SimpleNamespace(), workspace=SimpleNamespace(root=selected),
        workspace_paths=SimpleNamespace(), close=lambda: None,
    )
    monkeypatch.setattr(cli.first_run, "is_interactive_terminal", lambda: True)
    output = _Console()
    monkeypatch.setattr(cli, "console", output)
    seen: dict[str, object] = {}
    def create(args: object, **_: object) -> object:
        seen["args"] = args
        return application

    monkeypatch.setattr(cli, "_create_application", create)
    monkeypatch.setattr(cli, "_chat_loop", lambda _context: None)

    assert cli.main(["chat", "--workspace", str(selected)]) == 0

    assert Path(seen["args"].workspace).resolve() == selected.resolve()
    assert "\n".join(output.output).count(str(selected)) == 1


def test_chat_non_tty_keeps_current_directory_without_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = SimpleNamespace(
        session=SimpleNamespace(config={}), orchestrator=SimpleNamespace(), config={},
        paths=SimpleNamespace(), workspace=SimpleNamespace(root=Path.cwd()),
        workspace_paths=SimpleNamespace(), close=lambda: None,
    )
    monkeypatch.setattr(cli.first_run, "is_interactive_terminal", lambda: False)
    output = _Console()
    monkeypatch.setattr(cli, "console", output)
    monkeypatch.setattr(
        workspace_entry,
        "choose_directory_native",
        lambda: (_ for _ in ()).throw(AssertionError("picker")),
    )
    seen: dict[str, object] = {}

    def create(args: object, **_: object) -> object:
        seen["args"] = args
        return application

    monkeypatch.setattr(cli, "_create_application", create)
    monkeypatch.setattr(cli, "_chat_loop", lambda _context: None)

    assert cli.main(["chat"]) == 0

    assert not hasattr(seen["args"], "workspace")
    rendered = "\n".join(output.output)
    assert "Workspace ativo" not in rendered
    assert "[READ ONLY]" not in rendered


@pytest.mark.parametrize("command", ["/workspace", "/diretorio", "/pwd"])
def test_workspace_command_uses_active_context_root(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active_root = tmp_path / "active"
    active_root.mkdir()
    output = _Console()
    monkeypatch.setattr(command_handlers, "console", output)
    context = SimpleNamespace(workspace=SimpleNamespace(root=active_root.resolve()))

    handled, should_exit = handle_command(command, context)

    assert handled is True
    assert should_exit is False
    assert "\n".join(output.output).count(str(active_root.resolve())) == 1


def test_first_run_recovery_happens_before_workspace_display(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _Console()
    monkeypatch.setattr(cli, "console", output)
    monkeypatch.setattr(cli.first_run, "is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli.first_run, "prepare_chat_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli,
        "_create_application",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConfigNotFound("missing")),
    )
    monkeypatch.setattr(cli.first_run, "recover_first_run_config", lambda *args, **kwargs: 0)

    assert cli.main(["chat"]) == 0
    assert "Workspace ativo" not in "\n".join(output.output)
