import json
import os

import pytest

from agent.application import AgentApplication
from agent.health import standalone_checks
from agent.health.standalone import (
    render_health_report,
    run_standalone_health_check,
)
from agent.health_check import run_health_check
from agent.memory.memory import MemoryLoadError
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext
from tests.support.offline_scenarios import OfflineLegacyGateway


def _initialized_context(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    return paths, workspace


def _check(report, check_id):
    return next(item for item in report["checks"] if item["id"] == check_id)


def test_standalone_health_is_offline_ready_without_writing_report(tmp_path):
    paths, workspace = _initialized_context(tmp_path)

    report = run_standalone_health_check(
        app_paths=paths,
        workspace=workspace,
    )

    assert report["readiness"] == {
        "offline_ready": True,
        "workspace_readable": True,
        "workspace_writable": True,
        "operation_mode": "read_write",
        "backend_configured": True,
        "backend_connectivity": "not_checked",
    }
    assert report["errors"] == 0
    assert report["persistence"]["written"] is False
    assert not paths.health_report_file.exists()
    assert not paths.state_dir.exists()


def test_health_report_is_written_only_when_requested(tmp_path):
    paths, workspace = _initialized_context(tmp_path)

    report = run_standalone_health_check(
        app_paths=paths,
        workspace=workspace,
        write_report=True,
    )

    assert report["persistence"]["written"] is True
    assert json.loads(paths.health_report_file.read_text(encoding="utf-8")) == report


def test_missing_or_future_config_is_reported_without_bootstrap(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    missing_paths = AppPaths.discover(app_home=tmp_path / "missing-home", env={})

    missing = run_standalone_health_check(
        app_paths=missing_paths,
        workspace=workspace,
    )

    assert _check(missing, "config")["status"] == "error"
    assert missing["readiness"]["offline_ready"] is False
    assert not missing_paths.config_dir.exists()

    future_paths = AppPaths.discover(app_home=tmp_path / "future-home", env={})
    future_paths.config_dir.mkdir(parents=True)
    future_paths.config_file.write_text(
        json.dumps({"schema_version": 999}),
        encoding="utf-8",
    )
    future = run_standalone_health_check(
        app_paths=future_paths,
        workspace=workspace,
    )

    assert _check(future, "config")["status"] == "error"
    assert "futura" in _check(future, "config")["message"]


def test_doctor_rejects_application_paths_inside_site_packages(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package_home = tmp_path / "site-packages" / "local-llm-agent"
    paths = AppPaths(
        config_dir=package_home / "config",
        data_dir=package_home / "data",
        state_dir=package_home / "state",
        cache_dir=package_home / "cache",
        log_dir=package_home / "logs",
    )

    report = run_standalone_health_check(
        app_paths=paths,
        workspace=workspace,
    )

    path_check = _check(report, "paths")
    assert path_check["status"] == "error"
    assert all(
        details["in_site_packages"]
        for details in path_check["details"].values()
    )


def test_health_facade_supports_json_and_explicit_config_path(
    tmp_path,
    capsys,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    custom_config = tmp_path / "custom" / "settings.json"
    ConfigRepository(paths, config_path=custom_config).initialize()

    report = run_health_check(
        app_paths=paths,
        workspace=workspace,
        config_path=custom_config,
        output_format="json",
    )
    output = capsys.readouterr().out

    assert json.loads(output) == report
    assert report["config_path"] == str(custom_config.resolve())
    assert "RELATÓRIO DE SAÚDE" in render_health_report(report, "human")


def test_health_uses_effective_environment_and_rejects_unknown_provider(tmp_path):
    paths, workspace = _initialized_context(tmp_path)

    overridden = run_standalone_health_check(
        app_paths=paths,
        workspace=workspace,
        environment={
            "LLM_AGENT_MODEL": "environment-model",
            "LLM_AGENT_API_URL": "http://environment.example/v1/chat/completions",
        },
    )
    backend = _check(overridden, "backend")

    assert backend["details"]["model"] == "environment-model"
    assert backend["details"]["endpoint"] == "http://environment.example/v1/chat/completions"

    document = json.loads(paths.config_file.read_text(encoding="utf-8"))
    selected = document["default_model_profile"]
    document["model_profiles"][selected]["provider"] = "unknown"
    paths.config_file.write_text(json.dumps(document), encoding="utf-8")

    unsupported = run_standalone_health_check(
        app_paths=paths,
        workspace=workspace,
        environment={},
    )

    assert _check(unsupported, "backend")["status"] == "error"
    assert unsupported["readiness"]["offline_ready"] is False


def test_workspace_check_reports_read_only_mode(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    real_access = os.access

    def access(path, mode):
        if str(path) == str(workspace.resolve()) and mode & os.W_OK:
            return False
        return real_access(path, mode)

    monkeypatch.setattr(standalone_checks.os, "access", access)

    result, context = standalone_checks.check_workspace(workspace)

    assert context is not None
    assert result.status == "warning"
    assert result.details["readable"] is True
    assert result.details["writable"] is False


def test_doctor_rejects_the_corrupt_json_rejected_by_application_without_writes(
    tmp_path,
):
    paths, workspace = _initialized_context(tmp_path)
    context = WorkspaceContext.create(workspace)
    workspace_paths = paths.for_workspace(context.workspace_id)
    workspace_paths.data_dir.mkdir(parents=True)
    workspace_paths.memory_file.write_text('{"notes": ', encoding="utf-8")
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    report = run_standalone_health_check(
        app_paths=paths,
        workspace=context,
    )

    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert _check(report, "state")["status"] == "error"
    assert report["readiness"]["offline_ready"] is False
    assert before == after
    with pytest.raises(MemoryLoadError):
        AgentApplication.create(
            paths=paths,
            workspace=workspace,
            gateway=OfflineLegacyGateway("unused"),
            configure_logging=False,
        )


def test_doctor_checks_sqlite_integrity_read_only(tmp_path):
    paths, workspace = _initialized_context(tmp_path)
    context = WorkspaceContext.create(workspace)
    workspace_paths = paths.for_workspace(context.workspace_id)
    workspace_paths.data_dir.mkdir(parents=True)
    workspace_paths.memory_db_file.write_bytes(b"not-a-sqlite-database")
    before = workspace_paths.memory_db_file.read_bytes()

    report = run_standalone_health_check(
        app_paths=paths,
        workspace=context,
    )

    state = _check(report, "state")
    assert state["status"] == "error"
    assert state["details"]["memory_db"]["integrity"] == "error"
    assert report["readiness"]["offline_ready"] is False
    assert workspace_paths.memory_db_file.read_bytes() == before
