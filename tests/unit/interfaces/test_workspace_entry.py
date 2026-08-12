from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.interfaces.cli import app as cli
from agent.interfaces.cli.workspace_entry import canonical_workspace, choose_workspace


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


def test_choose_workspace_reprompts_invalid_path_then_accepts_other(tmp_path: Path) -> None:
    selected_root = tmp_path / "selected"
    selected_root.mkdir()
    console = _Console("2", str(tmp_path / "missing"), "2", str(selected_root))

    selected = choose_workspace(console=console, current=tmp_path)

    assert selected == selected_root.resolve()
    assert any("Workspace inválido" in item for item in console.output)
    assert not (tmp_path / "missing").exists()


def test_chat_tty_without_override_passes_selected_workspace_to_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = SimpleNamespace(
        session=SimpleNamespace(config={}),
        orchestrator=SimpleNamespace(),
        config={},
        paths=SimpleNamespace(),
        workspace=SimpleNamespace(root=tmp_path.resolve()),
        workspace_paths=SimpleNamespace(),
        closed=False,
        close=lambda: None,
    )
    selected = tmp_path / "selected"
    selected.mkdir()
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli.first_run, "is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli.console, "input", lambda prompt: str(selected) if "Pasta" in prompt else "2")
    def create(args: object, **_: object) -> object:
        seen["args"] = args
        return application

    monkeypatch.setattr(cli, "_create_application", create)
    monkeypatch.setattr(cli, "_chat_loop", lambda _context: None)

    assert cli.main(["chat"]) == 0

    assert Path(seen["args"].workspace).resolve() == selected.resolve()


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
    monkeypatch.setattr(cli.console, "input", lambda _: (_ for _ in ()).throw(AssertionError("chooser")))
    seen: dict[str, object] = {}
    def create(args: object, **_: object) -> object:
        seen["args"] = args
        return application

    monkeypatch.setattr(cli, "_create_application", create)
    monkeypatch.setattr(cli, "_chat_loop", lambda _context: None)

    assert cli.main(["chat", "--workspace", str(selected)]) == 0

    assert Path(seen["args"].workspace).resolve() == selected.resolve()


def test_chat_non_tty_keeps_current_directory_without_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = SimpleNamespace(
        session=SimpleNamespace(config={}), orchestrator=SimpleNamespace(), config={},
        paths=SimpleNamespace(), workspace=SimpleNamespace(root=Path.cwd()),
        workspace_paths=SimpleNamespace(), close=lambda: None,
    )
    monkeypatch.setattr(cli.first_run, "is_interactive_terminal", lambda: False)
    monkeypatch.setattr(cli.console, "input", lambda _: (_ for _ in ()).throw(AssertionError("prompt")))
    seen: dict[str, object] = {}

    def create(args: object, **_: object) -> object:
        seen["args"] = args
        return application

    monkeypatch.setattr(cli, "_create_application", create)
    monkeypatch.setattr(cli, "_chat_loop", lambda _context: None)

    assert cli.main(["chat"]) == 0

    assert not hasattr(seen["args"], "workspace")
