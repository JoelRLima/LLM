import json
import os
from dataclasses import fields
from pathlib import Path
from typing import Any, cast

import pytest

from llm_agent.agent.application import AgentApplication
from llm_agent.agent.health import standalone_checks
from llm_agent.agent.health.standalone import (
    render_health_report,
    run_standalone_health_check,
)
from llm_agent.agent.health_check import run_health_check
from llm_agent.agent.memory.memory import MemoryLoadError
from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.application.health import (
    HealthDiagnosticsRequest,
    HealthDiagnosticsResult,
    run_health_diagnostics,
)
from llm_agent.application.health import diagnostics as health_diagnostics
from llm_agent.workspace.context import WorkspaceContext
from llm_agent.workspace.paths import AppPaths
from tests.support.offline_scenarios import OfflineChatGateway


def _initialized_context(tmp_path: Path) -> tuple[AppPaths, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    return paths, workspace


def _check(report: dict[str, Any], check_id: str) -> Any:
    return next(item for item in report["checks"] if item["id"] == check_id)


def test_standalone_health_is_offline_ready_without_writing_report(tmp_path: Path) -> None:
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
    assert not paths.global_dir.exists()


def test_health_report_is_written_only_when_requested(tmp_path: Path) -> None:
    paths, workspace = _initialized_context(tmp_path)

    report = run_standalone_health_check(
        app_paths=paths,
        workspace=workspace,
        write_report=True,
    )

    assert report["persistence"]["written"] is True
    assert json.loads(paths.health_report_file.read_text(encoding="utf-8")) == report


def test_missing_or_future_config_is_reported_without_bootstrap(tmp_path: Path) -> None:
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


def test_doctor_rejects_application_paths_inside_site_packages(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    package_home = tmp_path / "site-packages" / "local-llm-agent"
    paths = AppPaths(
        home_dir=package_home,
        config_dir=package_home / "config",
        global_dir=package_home / "global",
        workspaces_dir=package_home / "workspaces",
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
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
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


def test_health_uses_effective_environment_and_rejects_unknown_provider(tmp_path: Path) -> None:
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


def test_workspace_check_reports_read_only_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    real_access = os.access

    def access(path: Any, mode: int) -> bool:
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
    tmp_path: Path,
) -> None:
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
            gateway=OfflineChatGateway("unused"),
            configure_logging=False,
        )


def test_doctor_checks_sqlite_integrity_read_only(tmp_path: Path) -> None:
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


def test_application_health_diagnostics_preserves_report_and_human_projection(tmp_path: Path) -> None:
    assert [field.name for field in fields(HealthDiagnosticsRequest)] == [
        "app_paths", "workspace", "config_path", "profile", "write_report", "online"
    ]
    assert [field.name for field in fields(HealthDiagnosticsResult)] == [
        "structured_report", "rendered_report", "offline_ready", "online_ready"
    ]
    paths, workspace = _initialized_context(tmp_path)
    context = WorkspaceContext.create(workspace)

    result = run_health_diagnostics(
        HealthDiagnosticsRequest(app_paths=paths, workspace=context)
    )

    assert result.structured_report["checks"]
    assert json.loads(json.dumps(result.structured_report)) == result.structured_report
    assert result.offline_ready is result.structured_report["readiness"]["offline_ready"]
    assert result.online_ready is None
    assert "RELATÓRIO DE SAÚDE DO AGENTE" in result.rendered_report
    assert "Prontidão offline:" in result.rendered_report


def test_application_health_diagnostics_forwards_write_report_and_online(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import llm_agent.application.health.diagnostics as diagnostics

    paths, workspace = _initialized_context(tmp_path)
    report = cast(dict[str, Any], run_standalone_health_check(app_paths=paths, workspace=workspace))
    report["online"] = {"state": "degraded", "online_ready": False}
    report["readiness"]["online_ready"] = False
    observed = {}

    def run(**kwargs: Any) -> Any:
        observed.update(kwargs)
        return report

    monkeypatch.setattr(diagnostics, "run_standalone_health_check", run)
    result = run_health_diagnostics(
        HealthDiagnosticsRequest(
            app_paths=paths,
            workspace=workspace,
            write_report=True,
            online=True,
        )
    )

    assert observed["write_report"] is True
    assert observed["online"] is True
    assert result.offline_ready is report["readiness"]["offline_ready"]
    assert result.online_ready is False


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (float("nan"), "health diagnostics report contains a non-finite number"),
        (float("inf"), "health diagnostics report contains a non-finite number"),
        (float("-inf"), "health diagnostics report contains a non-finite number"),
        ({1: "value"}, "health diagnostics report contains an invalid object"),
        (object(), "health diagnostics report contains a non-JSON value"),
        ([None] * 1_025, "health diagnostics report contains an oversized array"),
        ({str(index): None for index in range(1_025)}, "health diagnostics report contains an invalid object"),
        ("x" * 1_000_001, "health diagnostics report contains oversized text"),
    ],
    ids=["nan", "positive-infinity", "negative-infinity", "non-string-key",
         "non-json", "oversized-array", "oversized-object", "oversized-text"],
)
def test_json_value_validation_preserves_rejection_messages(value: object, message: str) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        health_diagnostics._validate_json_value(value, depth=0, budget=[20_000])


def test_json_value_validation_preserves_shared_budget_and_depth_limits() -> None:
    health_diagnostics._validate_json_value([1, 2], depth=0, budget=[3])
    with pytest.raises(ValueError, match="^health diagnostics report exceeds its bounded JSON shape$"):
        health_diagnostics._validate_json_value([1, 2], depth=0, budget=[2])
    health_diagnostics._validate_json_value(None, depth=16, budget=[1])
    with pytest.raises(ValueError, match="^health diagnostics report exceeds its bounded JSON shape$"):
        health_diagnostics._validate_json_value(None, depth=17, budget=[1])


def test_json_value_validation_preserves_container_boundaries_and_traversal() -> None:
    budget = [6]
    health_diagnostics._validate_json_value({"a": [None], "b": True}, depth=0, budget=budget)
    assert budget == [0]
    for value in ([None] * 1_024, {str(index): None for index in range(1_024)}, "x" * 1_000_000):
        health_diagnostics._validate_json_value(value, depth=0, budget=[20_000])
    nested: Any = None
    for _ in range(16):
        nested = [nested]
    health_diagnostics._validate_json_value(nested, depth=0, budget=[20_000])
    with pytest.raises(ValueError, match="^health diagnostics report exceeds its bounded JSON shape$"):
        health_diagnostics._validate_json_value([nested], depth=0, budget=[20_000])
    with pytest.raises(ValueError, match="^health diagnostics report contains oversized text$"):
        health_diagnostics._validate_json_value({"x" * 1_000_001: object()}, depth=0, budget=[20_000])


def test_json_value_validation_preserves_aggregate_node_limit() -> None:
    value = [[None] * 1_024 for _ in range(19)] + [[None] * 523]
    budget = [20_000]
    health_diagnostics._validate_json_value(value, depth=0, budget=budget)
    assert budget == [0]
    value[-1].append(None)
    budget = [20_000]
    with pytest.raises(ValueError, match="^health diagnostics report exceeds its bounded JSON shape$"):
        health_diagnostics._validate_json_value(value, depth=0, budget=budget)
    assert budget == [-1]


@pytest.mark.parametrize(
    ("changes", "online", "message"),
    [
        ({"extra": None}, False, "an unexpected schema"),
        ({"schema_version": True}, False, "an invalid schema version"),
        ({"summary": None}, False, "an invalid summary"),
        ({"config_path": None}, False, "an invalid config_path"),
        ({"workspace": 1}, False, "an invalid workspace"),
        ({"total_checks": -1}, False, "an invalid total_checks"),
        ({"ok": True}, False, "an invalid ok"),
        ({"warnings": 1.0}, False, "an invalid warnings"),
        ({"errors": -1}, False, "an invalid errors"),
        ({"app_paths": {}}, False, "invalid application paths"),
        ({"readiness": {}, "checks": None}, False, "invalid readiness fields"),
        ({"checks": [None] * 33, "persistence": {}}, False, "invalid checks"),
        ({"checks": [None]}, False, "an invalid check entry"),
        ({"checks": [{"id": 1, "name": "n", "status": "s", "message": "m", "details": None}]}, False, "invalid check text"),
        ({"checks": [{"id": "i", "name": "n", "status": "s", "message": "m", "details": None}]}, False, "invalid check details"),
        ({"persistence": {}}, False, "invalid persistence"),
        ({"online": None}, True, "invalid online diagnostics"),
    ],
)
def test_structured_report_validation_preserves_errors(tmp_path: Path, changes: dict[str, Any], online: bool, message: str) -> None:
    paths, workspace = _initialized_context(tmp_path)
    report = run_standalone_health_check(app_paths=paths, workspace=workspace)
    if online:
        report["online"] = {}
        report["readiness"]["online_ready"] = False
    report.update(changes)
    with pytest.raises(ValueError, match=f"^health diagnostics report has {message}$"):
        health_diagnostics._validate_structured_report(report, online)


@pytest.mark.parametrize(
    ("online", "field", "message"),
    [(False, "offline_ready", "readiness values"), (True, "online_ready", "online readiness")],
)
def test_structured_report_readiness_values_precede_checks(tmp_path: Path, online: bool, field: str, message: str) -> None:
    paths, workspace = _initialized_context(tmp_path)
    report = run_standalone_health_check(app_paths=paths, workspace=workspace)
    if online:
        report["online"] = {}
        report["readiness"]["online_ready"] = False
    report["readiness"][field] = 1
    report["checks"] = None
    with pytest.raises(ValueError, match=f"^health diagnostics report has invalid {message}$"):
        health_diagnostics._validate_structured_report(report, online)


def test_structured_report_keeps_schema_errors_ahead_of_readiness_errors(tmp_path: Path) -> None:
    paths, workspace = _initialized_context(tmp_path)
    report = run_standalone_health_check(app_paths=paths, workspace=workspace)
    report["generated_at"] = None
    report["readiness"] = {}

    with pytest.raises(ValueError, match="^health diagnostics report has an invalid generated_at$"):
        health_diagnostics._validate_structured_report(report, online=False)
