from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

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
    monkeypatch.setattr(process_tree, "_windows_system_directory", lambda: None)
    monkeypatch.setenv("SystemRoot", "relative-system-root")
    monkeypatch.delenv("WINDIR", raising=False)
    assert process_tree._trusted_taskkill_path() is None
    assert os.name == host_name
    assert type(Path.cwd()).__name__ == ("WindowsPath" if host_name == "nt" else "PosixPath")


def test_windows_cleanup_ignores_absolute_environment_roots(
    tmp_path: Path, monkeypatch
) -> None:
    host_name = os.name
    fake_root = tmp_path / "fake-windows"
    (fake_root / "System32").mkdir(parents=True)
    (fake_root / "System32" / "taskkill.exe").write_text("fake", encoding="utf-8")
    os_system = tmp_path / "os-system" / "System32"
    os_system.mkdir(parents=True)
    os_taskkill = os_system / "taskkill.exe"
    os_taskkill.write_text("native", encoding="utf-8")
    monkeypatch.setattr(process_tree, "os", SimpleNamespace(name="nt", environ=os.environ))
    monkeypatch.setattr(process_tree, "_windows_system_directory", lambda: str(os_system))
    monkeypatch.setenv("SystemRoot", str(fake_root))
    monkeypatch.setenv("WINDIR", str(fake_root))

    assert process_tree._trusted_taskkill_path() == str(os_taskkill.resolve())
    assert os.name == host_name
    assert type(Path.cwd()).__name__ == ("WindowsPath" if host_name == "nt" else "PosixPath")


@pytest.mark.skipif(os.name != "nt", reason="Win32 system directory unavailable")
def test_windows_cleanup_uses_real_os_system_directory() -> None:
    system_directory = process_tree._windows_system_directory()
    assert system_directory
    taskkill = process_tree._trusted_taskkill_path()
    assert taskkill
    assert Path(taskkill).parent.resolve() == Path(system_directory).resolve()
    assert Path(taskkill).name.casefold() == "taskkill.exe"
