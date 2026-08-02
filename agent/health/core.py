from __future__ import annotations

import sys
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.json"
MEMORY_PATH = PROJECT_ROOT / paths.MEMORY_FILE
MEMORY_BACKUP_DIR = PROJECT_ROOT / paths.MEMORY_BACKUP_DIR
MEMORY_RESTORE_DIR = PROJECT_ROOT / paths.RESTORE_POINTS_DIR
TEMP_ANALYSIS_DIR = PROJECT_ROOT / ".temp_analysis"
LOG_FILE = PROJECT_ROOT / paths.LOG_FILE
METRICS_FILE = PROJECT_ROOT / paths.METRICS_FILE
HEALTH_REPORT_PATH = PROJECT_ROOT / paths.HEALTH_REPORT_FILE

REQUIRED_CONFIG_KEYS = ["api_url", "model", "temperature", "max_tokens", "timeout", "default_system_prompt"]
EXPECTED_MEMORY_SECTIONS = ["project_map", "files_index", "todo", "notes", "analyzed_files"]
ESSENTIAL_SKILLS = ["file_reader", "file_writer", "python_executor", "grep", "directory_lister"]
LOG_SIZE_WARNING_BYTES = 10 * 1024 * 1024

__all__ = [
    "CONFIG_PATH",
    "ESSENTIAL_SKILLS",
    "EXPECTED_MEMORY_SECTIONS",
    "HEALTH_REPORT_PATH",
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
]

def ensure_sys_path() -> None:
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
