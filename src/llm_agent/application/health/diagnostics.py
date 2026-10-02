"""Application use case for product and Agent health diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from json import dumps
from pathlib import Path

from llm_agent.agent.health.standalone import (
    render_health_report,
    run_standalone_health_check,
)
from llm_agent.application.context import AppPaths, WorkspaceContext


@dataclass(frozen=True, slots=True)
class HealthDiagnosticsRequest:
    """Inputs required to execute the health diagnostics use case."""

    app_paths: AppPaths | None
    workspace: WorkspaceContext | str | Path | None
    config_path: str | Path | None = None
    profile: str | None = None
    write_report: bool = False
    online: bool = False


@dataclass(frozen=True, slots=True)
class HealthDiagnosticsResult:
    """Structured report and interface-safe readiness/rendering projections."""

    structured_report: Mapping[str, object]
    rendered_report: str
    offline_ready: bool
    online_ready: bool | None


def run_health_diagnostics(
    request: HealthDiagnosticsRequest,
) -> HealthDiagnosticsResult:
    """Run Agent health checks and project their result for interfaces."""
    app_paths = request.app_paths or AppPaths.discover()
    workspace = request.workspace or Path.cwd()
    report = run_standalone_health_check(
        app_paths=app_paths,
        workspace=workspace,
        config_path=request.config_path,
        profile=request.profile,
        write_report=request.write_report,
        online=request.online,
    )
    _validate_structured_report(report, request.online)
    readiness = report.get("readiness")
    readiness_map = readiness if isinstance(readiness, Mapping) else {}
    online_ready = (
        readiness_map.get("online_ready") is True
        if request.online
        else None
    )
    return HealthDiagnosticsResult(
        structured_report=report,
        rendered_report=render_health_report(report, "human"),
        offline_ready=readiness_map.get("offline_ready") is True,
        online_ready=online_ready,
    )


def _validate_structured_report(report: dict[str, object], online: bool) -> None:
    required = {
        "schema_version",
        "generated_at",
        "summary",
        "total_checks",
        "ok",
        "warnings",
        "errors",
        "workspace",
        "config_path",
        "app_paths",
        "readiness",
        "checks",
        "persistence",
    }
    expected = required | ({"online"} if online else set())
    if set(report) != expected:
        raise ValueError("health diagnostics report has an unexpected schema")
    if not isinstance(report["schema_version"], int) or isinstance(report["schema_version"], bool):
        raise ValueError("health diagnostics report has an invalid schema version")
    for field in ("generated_at", "summary", "config_path"):
        if not isinstance(report[field], str):
            raise ValueError(f"health diagnostics report has an invalid {field}")
    if report["workspace"] is not None and not isinstance(report["workspace"], str):
        raise ValueError("health diagnostics report has an invalid workspace")
    for field in ("total_checks", "ok", "warnings", "errors"):
        value = report[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"health diagnostics report has an invalid {field}")
    app_paths = report["app_paths"]
    if not isinstance(app_paths, dict) or set(app_paths) != {
        "home", "config", "global", "workspaces", "cache", "logs"
    } or any(not isinstance(value, str) for value in app_paths.values()):
        raise ValueError("health diagnostics report has invalid application paths")
    readiness = report["readiness"]
    readiness_fields = {
        "offline_ready", "workspace_readable", "workspace_writable",
        "operation_mode", "backend_configured", "backend_connectivity",
    }
    if online:
        readiness_fields.add("online_ready")
    if not isinstance(readiness, dict) or set(readiness) != readiness_fields:
        raise ValueError("health diagnostics report has invalid readiness fields")
    if any(
        not isinstance(readiness[field], bool)
        for field in ("offline_ready", "workspace_readable", "workspace_writable", "backend_configured")
    ):
        raise ValueError("health diagnostics report has invalid readiness values")
    if online and not isinstance(readiness["online_ready"], bool):
        raise ValueError("health diagnostics report has invalid online readiness")
    checks = report["checks"]
    if not isinstance(checks, list) or len(checks) > 32:
        raise ValueError("health diagnostics report has invalid checks")
    for check in checks:
        if not isinstance(check, dict) or not {"id", "name", "status", "message", "details"} <= set(check):
            raise ValueError("health diagnostics report has an invalid check entry")
        if any(not isinstance(check[field], str) for field in ("id", "name", "status", "message")):
            raise ValueError("health diagnostics report has invalid check text")
        if not isinstance(check["details"], dict):
            raise ValueError("health diagnostics report has invalid check details")
    persistence = report["persistence"]
    if (
        not isinstance(persistence, dict)
        or set(persistence) != {"requested", "written", "path"}
        or not isinstance(persistence["requested"], bool)
        or not isinstance(persistence["written"], bool)
        or not isinstance(persistence["path"], str)
    ):
        raise ValueError("health diagnostics report has invalid persistence")
    if online and not isinstance(report["online"], dict):
        raise ValueError("health diagnostics report has invalid online diagnostics")
    _validate_json_value(report, depth=0, budget=[20_000])
    try:
        dumps(report, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("health diagnostics report is not JSON serializable") from exc


def _validate_json_value(value: object, *, depth: int, budget: list[int]) -> None:
    budget[0] -= 1
    if budget[0] < 0 or depth > 16:
        raise ValueError("health diagnostics report exceeds its bounded JSON shape")
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value) > 1_000_000:
            raise ValueError("health diagnostics report contains oversized text")
        return
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("health diagnostics report contains a non-finite number")
        return
    if isinstance(value, dict):
        if len(value) > 1_024 or any(not isinstance(key, str) for key in value):
            raise ValueError("health diagnostics report contains an invalid object")
        for key, item in value.items():
            _validate_json_value(key, depth=depth + 1, budget=budget)
            _validate_json_value(item, depth=depth + 1, budget=budget)
        return
    if isinstance(value, list):
        if len(value) > 1_024:
            raise ValueError("health diagnostics report contains an oversized array")
        for item in value:
            _validate_json_value(item, depth=depth + 1, budget=budget)
        return
    raise ValueError("health diagnostics report contains a non-JSON value")
