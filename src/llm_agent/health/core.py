"""Agent-independent product and installation health checks."""

from __future__ import annotations

import importlib.resources
import os
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from llm_agent._version import VERSION
from llm_agent.application.context import AppPaths, WorkspaceContext


class ProductHealthStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class HealthCheck:
    name: str
    status: ProductHealthStatus
    message: str
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class HealthReport:
    checks: tuple[HealthCheck, ...]
    offline_ready: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "offline_ready": self.offline_ready,
            "checks": [
                {
                    "name": check.name,
                    "status": check.status.value,
                    "message": check.message,
                    "details": check.details,
                }
                for check in self.checks
            ],
        }


def _check_version() -> HealthCheck:
    valid = isinstance(VERSION, str) and bool(VERSION.strip())
    return HealthCheck(
        "product_version",
        ProductHealthStatus.OK if valid else ProductHealthStatus.ERROR,
        VERSION if valid else "product version unavailable",
        {"version": VERSION},
    )


def _check_resources() -> HealthCheck:
    try:
        resource = importlib.resources.files("llm_agent.resources").joinpath("default_config.json")
        data = resource.read_bytes()
        valid = bool(data)
    except (OSError, ModuleNotFoundError):
        valid = False
        data = b""
    return HealthCheck(
        "product_resources",
        ProductHealthStatus.OK if valid else ProductHealthStatus.ERROR,
        "packaged product resources available" if valid else "packaged product resources unavailable",
        {"default_config_bytes": len(data)},
    )


def _check_runtime_paths(paths: AppPaths) -> HealthCheck:
    values = {
        name: str(value)
        for name, value in {
            "home_dir": paths.home_dir,
            "config_dir": paths.config_dir,
            "global_dir": paths.global_dir,
            "workspaces_dir": paths.workspaces_dir,
            "cache_dir": paths.cache_dir,
            "log_dir": paths.log_dir,
        }.items()
    }
    valid = all(Path(value).is_absolute() for value in values.values())
    writable_parent = all(
        os.access(Path(value).parent, os.W_OK | os.X_OK)
        for value in values.values()
    )
    status = ProductHealthStatus.OK if valid and writable_parent else ProductHealthStatus.WARNING
    return HealthCheck(
        "runtime_paths",
        status,
        "runtime paths are absolute and writable" if status is ProductHealthStatus.OK else "runtime path parent is not writable",
        {**values, "writable_parent": writable_parent},
    )


def _check_python() -> HealthCheck:
    valid = sys.version_info[:2] >= (3, 10)
    version = ".".join(str(value) for value in sys.version_info[:3])
    return HealthCheck(
        "python",
        ProductHealthStatus.OK if valid else ProductHealthStatus.ERROR,
        f"Python {version}",
        {"version": version, "minimum": "3.10"},
    )


def _check_workspace(workspace: WorkspaceContext | str | Path | None) -> HealthCheck:
    if workspace is None:
        return HealthCheck(
            "workspace",
            ProductHealthStatus.OK,
            "workspace not requested",
            {"requested": False},
        )
    try:
        context = workspace if isinstance(workspace, WorkspaceContext) else WorkspaceContext.create(workspace)
        readable = os.access(context.root, os.R_OK | os.X_OK)
        writable = os.access(context.root, os.W_OK | os.X_OK)
        status = ProductHealthStatus.OK if readable and writable else ProductHealthStatus.WARNING
        return HealthCheck(
            "workspace",
            status,
            "workspace is readable and writable" if status is ProductHealthStatus.OK else "workspace access is limited",
            {"path": str(context.root), "workspace_id": context.workspace_id, "readable": readable, "writable": writable},
        )
    except (FileNotFoundError, NotADirectoryError, OSError, ValueError) as exc:
        return HealthCheck("workspace", ProductHealthStatus.ERROR, str(exc), {"requested": True})


def run_product_health_check(
    *,
    app_paths: AppPaths | None = None,
    workspace: WorkspaceContext | str | Path | None = None,
) -> HealthReport:
    """Run side-effect-free product checks without importing Agent modules."""

    paths = app_paths or AppPaths.discover()
    checks = (_check_version(), _check_resources(), _check_runtime_paths(paths), _check_python(), _check_workspace(workspace))
    return HealthReport(tuple(checks), all(check.status is not ProductHealthStatus.ERROR for check in checks))
