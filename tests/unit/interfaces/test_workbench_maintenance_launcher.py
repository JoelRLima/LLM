import io
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from llm_agent.interfaces.workbench import maintenance


def test_open_forwards_workspace_with_spaces_and_profile(monkeypatch):
    source = Path.cwd()
    root = source / maintenance.MANAGED_DIRECTORY
    candidate = root / "versions" / "candidate"
    workspace = Path.cwd() / "a workspace with spaces"
    captured = {}

    installation = maintenance.LocalInstallation(source)
    monkeypatch.setattr(installation, "pointer", lambda: {"current": "candidate"})
    monkeypatch.setattr(installation, "candidate", lambda _identifier: candidate)
    monkeypatch.setattr(installation, "configuration", lambda: root / "data" / "config.json")
    monkeypatch.setattr(maintenance, "read_json", lambda _path: {"package_fingerprint": "pkg"})
    monkeypatch.setattr(maintenance, "package_fingerprint", lambda _path: "pkg")
    monkeypatch.setattr(Path, "mkdir", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: io.StringIO())

    class Process:
        pid = 123

    def popen(arguments, **kwargs):
        captured["arguments"] = arguments
        captured["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(maintenance.subprocess, "Popen", popen)
    result = installation.open(workspace, "compact")

    assert result["status"] == "opened"
    assert str(workspace.resolve()) == captured["arguments"][captured["arguments"].index("--workspace") + 1]
    assert captured["arguments"][captured["arguments"].index("--profile") + 1] == "compact"
    assert captured["kwargs"]["cwd"] == candidate


def test_open_cli_returns_failure_when_open_fails(monkeypatch, capsys):
    def fail_open(_self, _workspace, _profile):
        raise RuntimeError("controlled open failure")

    monkeypatch.setattr(maintenance.LocalInstallation, "open", fail_open)
    code = maintenance.main(["open", "--source", str(Path.cwd()), "--workspace", str(Path.cwd() / "space path"), "--profile", "compact"])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_unified_batch_launcher_routes_open_and_management_options():
    root = Path(__file__).resolve().parents[3]
    launcher = (root / "Workbench.cmd").read_text(encoding="utf-8")
    powershell = (root / "Gerenciar_Workbench.ps1").read_text(encoding="utf-8")

    assert 'if /i "%~1"=="open" goto open_entry' in launcher
    assert 'if /i "%~1"=="manage" goto manage_entry' in launcher
    assert 'if /i "%~1"=="--workspace" goto parse_open_workspace' in launcher
    assert 'if /i "%~1"=="--profile" goto parse_open_profile' in launcher
    assert '-Operation Open -Workspace "%WORKSPACE%" -Profile "%PROFILE%"' in launcher
    assert "[string]$Profile" in powershell and "'--profile', $Profile" in powershell


def test_unified_batch_launcher_preserves_powershell_exit_code():
    root = Path(__file__).resolve().parents[3]
    launcher = (root / "Workbench.cmd").read_text(encoding="utf-8")
    assert 'set "RESULT=%ERRORLEVEL%"' in launcher
    assert "exit /b %RESULT%" in launcher


@pytest.mark.skipif(os.name != "nt", reason="Windows batch launcher integration test")
def test_unified_launcher_open_help_does_not_require_installation(tmp_path):
    root = Path(__file__).resolve().parents[3]
    shutil.copy2(root / "Workbench.cmd", tmp_path / "Workbench.cmd")
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "Workbench.cmd", "open", "--help"],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout
    assert "--profile seleciona um perfil de modelo" in completed.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows batch launcher integration test")
@pytest.mark.parametrize("exit_code", [0, 7])
def test_unified_batch_launcher_opens_managed_workbench_and_preserves_status(tmp_path, exit_code):
    root = Path(__file__).resolve().parents[3]
    shutil.copy2(root / "Workbench.cmd", tmp_path / "Workbench.cmd")
    (tmp_path / ".workbench-managed").mkdir()
    (tmp_path / ".workbench-managed" / "current.json").write_text("{}", encoding="utf-8")
    record = tmp_path / "received.json"
    workspace = tmp_path / "workspace with spaces"
    workspace.mkdir()
    fake_ps = tmp_path / "Gerenciar_Workbench.ps1"
    fake_ps.write_text(
        "param([string]$Operation, [string]$Workspace, [string]$Profile)\n"
        "$payload = @{ operation = $Operation; workspace = $Workspace; profile = $Profile } | ConvertTo-Json -Compress\n"
        "[System.IO.File]::WriteAllText($env:WRAPPER_TEST_RECORD, $payload)\n"
        "exit [int]$env:WRAPPER_TEST_EXIT\n",
        encoding="utf-8",
    )
    env = dict(os.environ, WRAPPER_TEST_RECORD=str(record), WRAPPER_TEST_EXIT=str(exit_code))
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "Workbench.cmd", "open", "--workspace", str(workspace), "--profile", "compact"],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
        check=False,
    )

    assert record.exists(), completed.stdout
    assert json.loads(record.read_text(encoding="utf-8")) == {
        "operation": "Open",
        "workspace": str(workspace),
        "profile": "compact",
    }
    assert completed.returncode == exit_code
