"""Local maintenance lifecycle and native UI update boundary tests."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_agent.interfaces.workbench.maintenance import (
    LocalInstallation,
    package_fingerprint,
    source_fingerprint,
    write_json,
)
from llm_agent.interfaces.workbench.updates import WorkbenchUpdates
from llm_agent.interfaces.workbench.webview import WorkbenchBridge


@pytest.fixture
def local_source(tmp_path: Path) -> Path:
    (tmp_path / "src/llm_agent").mkdir(parents=True)
    (tmp_path / "src/llm_agent/__init__.py").write_text("VERSION = '1'\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("local app", encoding="utf-8")
    return tmp_path


def installed(source: Path, identifier: str = "abc-123") -> tuple[LocalInstallation, Path]:
    installation = LocalInstallation(source)
    candidate = installation.candidate(identifier)
    package = candidate / "runtime/Lib/site-packages/llm_agent"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VERSION = '1'\n", encoding="utf-8")
    write_json(candidate / "installation.json", {"schema": 1, "source_root": str(source), "candidate": identifier,
               "source_fingerprint": source_fingerprint(source), "package_fingerprint": package_fingerprint(package), "version": "1"})
    write_json(installation.root / "current.json", {"schema": 1, "current": identifier, "previous": None})
    return installation, candidate / "runtime"


def test_update_detects_code_change_without_version_bump(local_source: Path):
    _, runtime = installed(local_source)
    service = WorkbenchUpdates(runtime)
    assert service.status()["available"] is False
    (local_source / "src/llm_agent/__init__.py").write_text("VERSION = '1'\nNEW = True\n", encoding="utf-8")
    assert service.status()["available"] is True
    assert service.status()["version"] == "1"


def test_bytecode_and_user_reports_do_not_offer_update(local_source: Path):
    original = source_fingerprint(local_source)
    cache = local_source / "src/llm_agent/__pycache__"
    cache.mkdir()
    (cache / "module.pyc").write_bytes(b"bytecode")
    (local_source / "report.json").write_text("{}", encoding="utf-8")
    assert source_fingerprint(local_source) == original


def test_styles_and_manager_changes_offer_update(local_source: Path):
    original = source_fingerprint(local_source)
    (local_source / "src/llm_agent/style.css").write_text("body {}", encoding="utf-8")
    assert source_fingerprint(local_source) != original
    original = source_fingerprint(local_source)
    (local_source / "Gerenciar_Workbench.ps1").write_text("changed", encoding="utf-8")
    assert source_fingerprint(local_source) != original


def test_foreign_installation_identity_is_closed(local_source: Path):
    _, runtime = installed(local_source)
    metadata = runtime.parent / "installation.json"
    value = json.loads(metadata.read_text(encoding="utf-8"))
    value["candidate"] = "../outside"
    write_json(metadata, value)
    status = WorkbenchUpdates(runtime).status()
    assert status["state"] == "error"
    assert not status["available"]


@pytest.mark.parametrize("current", [[], {"candidate": "abc-123"}, 17])
def test_malformed_current_pointer_returns_update_error(local_source: Path, current):
    installation, runtime = installed(local_source)
    write_json(installation.root / "current.json", {"schema": 1, "current": current, "previous": None})

    status = WorkbenchUpdates(runtime).status()

    assert status["state"] == "error"
    assert "apontamento" in status["message"]


def test_malformed_candidate_metadata_returns_update_error(local_source: Path):
    _, runtime = installed(local_source)
    metadata = runtime.parent / "installation.json"
    write_json(metadata, {"schema": 1, "source_root": str(local_source), "candidate": ["abc-123"],
                         "source_fingerprint": "fingerprint", "package_fingerprint": "package", "version": "1"})

    status = WorkbenchUpdates(runtime).status()

    assert status["state"] == "error"
    assert "Identidade" in status["message"]


def test_non_object_installation_metadata_returns_update_error(local_source: Path):
    _, runtime = installed(local_source)
    (runtime.parent / "installation.json").write_text("[]", encoding="utf-8")

    status = WorkbenchUpdates(runtime).status()

    assert status["state"] == "error"
    assert "Metadados invalidos" in status["message"]


@pytest.mark.parametrize("stdout", ["[]", "null", '"invalid"', '{"status":"unchanged"}', '{"message":"ok"}'])
def test_apply_rejects_invalid_manager_response_without_state_change(local_source: Path, monkeypatch, stdout: str):
    installation, runtime = installed(local_source)
    manager = local_source / "Gerenciar_Workbench.ps1"
    manager.write_text("stub", encoding="utf-8")
    before = installation.pointer()
    monkeypatch.setattr(
        "llm_agent.interfaces.workbench.updates.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
    )

    with pytest.raises(RuntimeError, match="resposta (invalida|incompleta)"):
        WorkbenchUpdates(runtime).apply()

    assert installation.pointer() == before


def test_candidate_cannot_escape_managed_versions(local_source: Path):
    installation = LocalInstallation(local_source)
    with pytest.raises(ValueError):
        installation.candidate("../../old-f1")


def test_uninstall_keeps_history_data_and_old_f1(local_source: Path):
    installation, runtime = installed(local_source)
    data = installation.root / "data"
    data.mkdir()
    (data / "history.json").write_text("history", encoding="utf-8")
    f1 = local_source / ".workbench-installed-f1"
    f1.mkdir()
    assert installation.uninstall()["status"] == "uninstalled"
    assert not installation.pointer()
    assert (installation.root / "uninstalled.json").is_file()
    assert runtime.is_dir()
    assert (data / "history.json").read_text(encoding="utf-8") == "history"
    assert f1.is_dir()
    assert WorkbenchUpdates(runtime).status()["state"] == "uninstalled"


def test_rollback_swaps_pointer_and_requires_restart(local_source: Path):
    installation, old_runtime = installed(local_source, "abc-123")
    _, new_runtime = installed(local_source, "def-456")
    write_json(installation.root / "current.json", {"schema": 1, "current": "def-456", "previous": "abc-123"})
    assert WorkbenchUpdates(old_runtime).status()["restart_required"]
    assert installation.rollback()["status"] == "restored"
    assert installation.pointer()["current"] == "abc-123"
    assert WorkbenchUpdates(new_runtime).status()["restart_required"]


def test_tampered_rollback_leaves_current_pointer(local_source: Path):
    installation, old_runtime = installed(local_source, "abc-123")
    installed(local_source, "def-456")
    write_json(installation.root / "current.json", {"schema": 1, "current": "def-456", "previous": "abc-123"})
    (old_runtime / "Lib/site-packages/llm_agent/__init__.py").write_text("tampered", encoding="utf-8")
    with pytest.raises(RuntimeError, match="alterada"):
        installation.rollback()
    assert installation.pointer()["current"] == "def-456"


def test_missing_dependencies_preserve_active_candidate(local_source: Path, monkeypatch):
    installation, _ = installed(local_source)
    (local_source / "src/llm_agent/changed.py").write_text("x = 1", encoding="utf-8")
    monkeypatch.setattr(installation, "_seed", lambda: (_ for _ in ()).throw(RuntimeError("desktop ausente")))
    before = installation.pointer()
    with pytest.raises(RuntimeError, match="desktop ausente"):
        installation.install()
    assert installation.pointer() == before


def test_failed_package_build_never_promotes_candidate(local_source: Path, monkeypatch):
    installation, runtime = installed(local_source)
    (local_source / "src/llm_agent/changed.py").write_text("x = 1", encoding="utf-8")
    monkeypatch.setattr(installation, "_seed", lambda: runtime)
    calls = []

    def failed(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="build failed")

    monkeypatch.setattr("llm_agent.interfaces.workbench.maintenance.subprocess.run", failed)
    before = installation.pointer()
    with pytest.raises(RuntimeError, match="preservada"):
        installation.install()
    assert installation.pointer() == before
    assert calls[0][2:6] == ["pip", "wheel", "--no-index", "--no-deps"]


def test_bridge_rejects_update_and_restart_while_task_active():
    bridge = WorkbenchBridge(session=SimpleNamespace(workspace=SimpleNamespace(root=Path.cwd())))
    bridge._active_request_id = "request-1"
    assert bridge.apply_update()["accepted"] is False
    assert bridge.restart_workbench()["accepted"] is False


def test_bridge_does_not_admit_task_while_installing():
    bridge = WorkbenchBridge(session=SimpleNamespace(workspace=SimpleNamespace(root=Path.cwd())))
    bridge._maintenance_running = True
    started = threading.Event()
    result = bridge._start_request(lambda *_: started.set())
    assert not result["accepted"]
    assert not started.is_set()


def test_unmanaged_installation_does_not_pretend_to_update(tmp_path: Path):
    status = WorkbenchUpdates(tmp_path / "runtime").status()
    assert status["state"] == "unmanaged"
    assert not status["available"]


def test_tampered_current_is_offered_repair_even_with_unchanged_source(local_source: Path, monkeypatch):
    installation, runtime = installed(local_source)
    (runtime / "Lib/site-packages/llm_agent/__init__.py").write_text("tampered", encoding="utf-8")
    assert WorkbenchUpdates(runtime).status()["available"]
    monkeypatch.setattr(installation, "_seed", lambda: (_ for _ in ()).throw(RuntimeError("repair reached")))
    with pytest.raises(RuntimeError, match="repair reached"):
        installation.install()


def test_concurrent_maintenance_cannot_deactivate_installation(local_source: Path):
    installation, _ = installed(local_source)
    with installation.locked(), pytest.raises((RuntimeError, OSError)):
        LocalInstallation(local_source).uninstall()
    assert installation.pointer()["current"] == "abc-123"


def test_update_panel_interactions():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node unavailable; run the same harness in the Node REPL")
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run([node, str(root / "tests/support/workbench_updates_ui.cjs"),
                             str(root / "src/llm_agent/interfaces/workbench/static/workbench-updates.js")],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_legacy_configuration_is_copied_once_without_changing_source(local_source: Path):
    source = local_source / "config.json"
    original = '{"api_url":"http://127.0.0.1:8080/v1/chat/completions","model":"default"}'
    source.write_text(original, encoding="utf-8")
    installation = LocalInstallation(local_source)
    target = installation.configuration()
    assert source.read_text(encoding="utf-8") == original
    assert json.loads(target.read_text(encoding="utf-8"))["schema_version"] == 1
    first = target.read_bytes()
    source.write_text('{"model":"other"}', encoding="utf-8")
    assert installation.configuration() == target
    assert target.read_bytes() == first


def test_invalid_configuration_is_not_relaxed(local_source: Path):
    (local_source / "config.json").write_text('{"max_tokens":"invalid"}', encoding="utf-8")
    with pytest.raises(RuntimeError):
        LocalInstallation(local_source).configuration()
