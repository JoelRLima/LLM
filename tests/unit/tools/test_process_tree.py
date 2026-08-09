from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from agent.tools import process_tree


class _Process:
    pid = 1234

    def __init__(self) -> None:
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 1
        return self.returncode

    def terminate(self):
        self.returncode = 1

    def kill(self):
        self.returncode = 1


def test_windows_cleanup_uses_canonical_taskkill_not_workspace_shadow(
    tmp_path: Path, monkeypatch
) -> None:
    host_name = os.name
    workspace_shadow = tmp_path / "taskkill.exe"
    workspace_shadow.write_text("sentinel", encoding="utf-8")
    trusted = tmp_path / "system32" / "taskkill.exe"
    trusted.parent.mkdir()
    trusted.write_text("trusted", encoding="utf-8")
    command: list[str] = []

    monkeypatch.setattr(process_tree, "os", SimpleNamespace(name="nt", environ=os.environ))
    monkeypatch.setattr(process_tree, "terminate_windows_job", lambda _job: False)
    monkeypatch.setattr(process_tree, "_trusted_taskkill_path", lambda: str(trusted))

    def fake_run(argv, **_kwargs):
        command.extend(argv)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(process_tree.subprocess, "run", fake_run)
    assert process_tree._terminate_windows_process(_Process(), None) is None
    assert command[0] == str(trusted)
    assert command[0] != str(workspace_shadow)
    assert os.name == host_name
    assert type(Path.cwd()).__name__ == ("WindowsPath" if host_name == "nt" else "PosixPath")


def test_windows_cleanup_does_not_use_relative_system_root(monkeypatch) -> None:
    host_name = os.name
    monkeypatch.setattr(process_tree, "os", SimpleNamespace(name="nt", environ=os.environ))
    monkeypatch.setenv("SystemRoot", "relative-system-root")
    monkeypatch.delenv("WINDIR", raising=False)
    assert process_tree._trusted_taskkill_path() is None
    assert os.name == host_name
    assert type(Path.cwd()).__name__ == ("WindowsPath" if host_name == "nt" else "PosixPath")
