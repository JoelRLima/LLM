"""Offline health reporting for an installed standalone application."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from agent.health.contracts import (
    STATUS_ERROR,
    STATUS_ICON,
    STATUS_OK,
    STATUS_WARNING,
    CheckResult,
)
from agent.health.standalone_checks import (
    check_backend,
    check_config,
    check_package,
    check_paths,
    check_python,
    check_state,
    check_workspace,
)
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext

OutputFormat = Literal["human", "json"]


def _summary(errors: int, offline_ready: bool, operation_mode: str) -> str:
    if not offline_ready:
        return f"Foram encontrados {errors} erro(s) bloqueante(s)."
    if operation_mode == "read_only":
        return "Sistema pronto para inicialização em modo somente leitura."
    return "Sistema pronto para inicialização offline."


def _report(
    checks: list[tuple[str, CheckResult]],
    app_paths: AppPaths,
    workspace: WorkspaceContext | None,
    config_path: Path,
) -> dict[str, Any]:
    serialized = [{"id": key, **result.to_dict()} for key, result in checks]
    counts = {
        "ok": sum(item.status == STATUS_OK for _, item in checks),
        "warnings": sum(item.status == STATUS_WARNING for _, item in checks),
        "errors": sum(item.status == STATUS_ERROR for _, item in checks),
    }
    offline_ready = counts["errors"] == 0
    backend = next(result for key, result in checks if key == "backend")
    workspace_check = next(result for key, result in checks if key == "workspace")
    workspace_readable = bool(workspace_check.details.get("readable"))
    workspace_writable = bool(workspace_check.details.get("writable"))
    if not workspace_readable:
        operation_mode = "unavailable"
    elif workspace_writable:
        operation_mode = "read_write"
    else:
        operation_mode = "read_only"
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": _summary(counts["errors"], offline_ready, operation_mode),
        "total_checks": len(checks),
        **counts,
        "workspace": str(workspace.root) if workspace else None,
        "config_path": str(config_path),
        "app_paths": {
            "config": str(app_paths.config_dir),
            "data": str(app_paths.data_dir),
            "state": str(app_paths.state_dir),
            "cache": str(app_paths.cache_dir),
            "logs": str(app_paths.log_dir),
        },
        "readiness": {
            "offline_ready": offline_ready,
            "workspace_readable": workspace_readable,
            "workspace_writable": workspace_writable,
            "operation_mode": operation_mode,
            "backend_configured": bool(backend.details.get("configured")),
            "backend_connectivity": "not_checked",
        },
        "checks": serialized,
    }


def render_health_report(
    report: dict[str, Any],
    output_format: OutputFormat = "human",
) -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    if output_format != "human":
        raise ValueError(f"Formato de relatório inválido: {output_format}")
    lines = ["RELATÓRIO DE SAÚDE DO AGENTE", "=" * 34]
    for check in report["checks"]:
        icon = STATUS_ICON.get(check["status"], "?")
        lines.extend((f"{icon} {check['name']}", f"   {check['message']}"))
    readiness = report["readiness"]
    lines.extend(
        (
            "-" * 34,
            report["summary"],
            f"Prontidão offline: {'SIM' if readiness['offline_ready'] else 'NÃO'}",
            f"Modo do workspace: {str(readiness['operation_mode']).upper()}",
            "Conectividade do backend: NÃO TESTADA",
        )
    )
    return "\n".join(lines)


def write_health_report(report: dict[str, Any], app_paths: AppPaths) -> Path:
    path = Path(app_paths.health_report_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def run_standalone_health_check(
    *,
    app_paths: AppPaths,
    workspace: WorkspaceContext | str | Path,
    config_path: str | Path | None = None,
    profile: str | None = None,
    environment: Mapping[str, str] | None = None,
    write_report: bool = False,
) -> dict[str, Any]:
    workspace_result, workspace_context = check_workspace(workspace)
    config_result, config, resolved_config_path = check_config(
        app_paths,
        config_path,
        overrides=(
            {"default_model_profile": profile}
            if profile is not None
            else None
        ),
        environment=environment,
    )
    checks = [
        ("package", check_package()),
        ("python", check_python()),
        ("paths", check_paths(app_paths)),
        ("config", config_result),
        ("workspace", workspace_result),
        ("state", check_state(app_paths, workspace_context)),
        ("backend", check_backend(config)),
    ]
    report = _report(
        checks,
        app_paths,
        workspace_context,
        resolved_config_path,
    )
    report["persistence"] = {
        "requested": write_report,
        "written": False,
        "path": str(app_paths.health_report_file),
    }
    if write_report:
        report["persistence"]["written"] = True
        try:
            write_health_report(report, app_paths)
        except Exception:
            report["persistence"]["written"] = False
            raise
    return report


__all__ = [
    "OutputFormat",
    "render_health_report",
    "run_standalone_health_check",
    "write_health_report",
]
