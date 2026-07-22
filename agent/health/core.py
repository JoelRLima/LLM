from __future__ import annotations

import sys
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict

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

STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_ERROR = "error"
STATUS_ICON = {STATUS_OK: "OK", STATUS_WARNING: "AVISO", STATUS_ERROR: "ERRO"}


@dataclass
class CheckResult:
    name: str
    status: str = STATUS_OK
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def ensure_sys_path() -> None:
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def safe_check(name: str, function: Callable[[], object]) -> CheckResult:
    try:
        result = function()
        if isinstance(result, CheckResult):
            return result
        return CheckResult(name, STATUS_WARNING, "Verificação não retornou CheckResult.", {"raw_result": str(result)})
    except Exception as exc:
        return CheckResult(name, STATUS_ERROR, f"Falha inesperada: {exc}", {"traceback": traceback.format_exc()})
