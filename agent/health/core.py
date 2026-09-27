from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from agent.health.contracts import (
    STATUS_ERROR,
    STATUS_ICON,
    STATUS_OK,
    STATUS_WARNING,
    CheckResult,
    safe_check,
)
from agent.runtime import paths
from agent.runtime.paths import AppPaths, WorkspacePaths

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.json"
_, _legacy_memory_file, _legacy_memory_backup_dir = paths.legacy_memory_paths()
MEMORY_PATH = PROJECT_ROOT / Path(_legacy_memory_file)
MEMORY_BACKUP_DIR = PROJECT_ROOT / Path(_legacy_memory_backup_dir)
MEMORY_RESTORE_DIR = PROJECT_ROOT / Path(paths.legacy_restore_points_dir())
TEMP_ANALYSIS_DIR = PROJECT_ROOT / ".temp_analysis"
LOG_FILE = PROJECT_ROOT / Path(paths.legacy_log_file())
METRICS_FILE = PROJECT_ROOT / Path(paths.legacy_metrics_file())
HEALTH_REPORT_PATH = PROJECT_ROOT / Path(paths.legacy_health_report_file())

REQUIRED_CONFIG_KEYS = ["api_url", "model", "temperature", "max_tokens", "timeout", "default_system_prompt"]
EXPECTED_MEMORY_SECTIONS = ["project_map", "files_index", "todo", "notes", "analyzed_files"]
ESSENTIAL_SKILLS = ["file_reader", "file_writer", "python_executor", "grep", "directory_lister"]
LOG_SIZE_WARNING_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class HealthPathContext:
    """Resolved paths used by the compatibility health checks."""

    config_file: Path
    memory_file: Path
    memory_backup_dir: Path
    restore_points_dir: Path
    temp_analysis_dir: Path
    log_file: Path
    metrics_file: Path
    health_report_file: Path
    project_root: Path


def resolve_health_paths(
    *,
    app_paths: AppPaths | None = None,
    workspace_paths: WorkspacePaths | None = None,
    workspace_root: str | Path | None = None,
) -> HealthPathContext:
    """Bind health checks to the resolved application/workspace identity."""

    selected_workspace_root = (
        Path(workspace_root).expanduser().resolve()
        if workspace_root is not None
        else PROJECT_ROOT
    )
    return HealthPathContext(
        config_file=Path(app_paths.config_file) if app_paths is not None else CONFIG_PATH,
        memory_file=(
            Path(workspace_paths.memory_file)
            if workspace_paths is not None
            else MEMORY_PATH
        ),
        memory_backup_dir=(
            Path(workspace_paths.memory_backup_dir)
            if workspace_paths is not None
            else MEMORY_BACKUP_DIR
        ),
        restore_points_dir=(
            Path(workspace_paths.restore_points_dir)
            if workspace_paths is not None
            else MEMORY_RESTORE_DIR
        ),
        temp_analysis_dir=(
            selected_workspace_root / ".temp_analysis"
            if workspace_root is not None
            else TEMP_ANALYSIS_DIR
        ),
        log_file=(Path(app_paths.log_file) if app_paths is not None else LOG_FILE),
        metrics_file=(
            Path(workspace_paths.metrics_file)
            if workspace_paths is not None
            else METRICS_FILE
        ),
        health_report_file=(
            Path(app_paths.health_report_file)
            if app_paths is not None
            else HEALTH_REPORT_PATH
        ),
        project_root=selected_workspace_root,
    )

__all__ = [
    "CONFIG_PATH",
    "ESSENTIAL_SKILLS",
    "EXPECTED_MEMORY_SECTIONS",
    "HEALTH_REPORT_PATH",
    "HealthPathContext",
    "LOG_FILE",
    "LOG_SIZE_WARNING_BYTES",
    "MEMORY_BACKUP_DIR",
    "MEMORY_PATH",
    "MEMORY_RESTORE_DIR",
    "PROJECT_ROOT",
    "REQUIRED_CONFIG_KEYS",
    "STATUS_ERROR",
    "STATUS_ICON",
    "STATUS_OK",
    "STATUS_WARNING",
    "TEMP_ANALYSIS_DIR",
    "CheckResult",
    "ensure_sys_path",
    "safe_check",
    "resolve_health_paths",
]

def ensure_sys_path() -> None:
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
