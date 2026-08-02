import json
import os
from pathlib import Path

import pytest

from agent.health import state_integrity
from agent.health.standalone import run_standalone_health_check
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext


def _symlink_or_skip(
    link: Path,
    target: Path,
    *,
    target_is_directory: bool = False,
) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        if os.name == "nt":
            pytest.skip(f"Criação de link não permitida neste Windows: {exc}")
        raise


def _initialized_context(
    tmp_path: Path,
) -> tuple[AppPaths, WorkspaceContext]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    return paths, WorkspaceContext.create(workspace)


def _state_check(report: dict[str, object]) -> dict[str, object]:
    checks = report["checks"]
    assert isinstance(checks, list)
    return next(item for item in checks if item["id"] == "state")


def test_doctor_rejects_json_symlink_without_reading_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, workspace = _initialized_context(tmp_path)
    workspace_paths = paths.for_workspace(workspace.workspace_id)
    workspace_paths.data_dir.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"notes": {"secret": True}}), encoding="utf-8")
    _symlink_or_skip(workspace_paths.memory_file, outside)

    def unexpected_read(*args: object, **kwargs: object) -> None:
        del args, kwargs
        pytest.fail("doctor não deveria ler um agent_memory.json link-like")

    monkeypatch.setattr(state_integrity, "read_json_object", unexpected_read)

    report = run_standalone_health_check(app_paths=paths, workspace=workspace)
    state = _state_check(report)
    details = state["details"]

    assert state["status"] == "error"
    assert details["memory"]["link_like"] is True
    assert report["readiness"]["offline_ready"] is False
    assert json.loads(outside.read_text(encoding="utf-8"))["notes"]["secret"] is True


def test_doctor_rejects_sqlite_symlink_without_connecting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, workspace = _initialized_context(tmp_path)
    workspace_paths = paths.for_workspace(workspace.workspace_id)
    workspace_paths.data_dir.mkdir(parents=True)
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"nao deve ser aberto")
    _symlink_or_skip(workspace_paths.memory_db_file, outside)

    def unexpected_connect(*args: object, **kwargs: object) -> None:
        del args, kwargs
        pytest.fail("doctor não deveria conectar em agent_memory.db link-like")

    monkeypatch.setattr(state_integrity.sqlite3, "connect", unexpected_connect)

    report = run_standalone_health_check(app_paths=paths, workspace=workspace)
    state = _state_check(report)
    details = state["details"]

    assert state["status"] == "error"
    assert details["memory_db"]["link_like"] is True
    assert report["readiness"]["offline_ready"] is False
    assert outside.read_bytes() == b"nao deve ser aberto"


def test_doctor_rejects_linked_backup_directory_without_listing_it(
    tmp_path: Path,
) -> None:
    paths, workspace = _initialized_context(tmp_path)
    workspace_paths = paths.for_workspace(workspace.workspace_id)
    workspace_paths.data_dir.mkdir(parents=True)
    outside = tmp_path / "outside-backups"
    outside.mkdir()
    _symlink_or_skip(
        workspace_paths.memory_backup_dir,
        outside,
        target_is_directory=True,
    )

    report = run_standalone_health_check(app_paths=paths, workspace=workspace)
    state = _state_check(report)
    details = state["details"]

    assert state["status"] == "error"
    assert details["memory_backups"]["link_like"] is True
    assert report["readiness"]["offline_ready"] is False
    assert list(outside.iterdir()) == []
