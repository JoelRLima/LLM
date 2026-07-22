from __future__ import annotations

import importlib
import os
import tempfile
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.health.core import (
    ESSENTIAL_SKILLS,
    LOG_FILE,
    LOG_SIZE_WARNING_BYTES,
    MEMORY_BACKUP_DIR,
    MEMORY_RESTORE_DIR,
    METRICS_FILE,
    PROJECT_ROOT,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_WARNING,
    TEMP_ANALYSIS_DIR,
    CheckResult,
    ensure_sys_path,
)


def check_orphan_dirs() -> CheckResult:
    details: Dict[str, Any] = {}
    warnings = []
    for key, directory in (("temp_analysis", TEMP_ANALYSIS_DIR), ("restore_dir", MEMORY_RESTORE_DIR)):
        exists = directory.exists()
        details[f"{key}_exists"] = exists
        if exists:
            try:
                count = len(list(directory.iterdir()))
            except OSError as exc:
                details[f"{key}_error"] = str(exc)
                count = 0
            details[f"{key}_entry_count"] = count
            if count or key == "restore_dir":
                warnings.append(f"{directory.name} contém {count} item(ns)")
    status = STATUS_WARNING if warnings else STATUS_OK
    return CheckResult("Diretórios órfãos", status, "; ".join(warnings) or "Nenhum diretório órfão encontrado.", details)


def check_permissions() -> CheckResult:
    details: Dict[str, Any] = {}
    problems = []
    for label, directory in (("project_root", PROJECT_ROOT), ("temp_analysis", TEMP_ANALYSIS_DIR), ("memory_backups", MEMORY_BACKUP_DIR)):
        if directory != PROJECT_ROOT and not directory.exists():
            details[f"{label}_writable"] = None
            continue
        valid, error = test_write_read_delete(directory)
        details[f"{label}_writable"] = valid
        if not valid:
            problems.append(f"{label} ({error})")
    status = STATUS_ERROR if problems else STATUS_OK
    message = "Problemas de permissão: " + "; ".join(problems) if problems else "Leitura e escrita funcionando."
    return CheckResult("Permissões de leitura/escrita", status, message, details)


def test_write_read_delete(directory: Path) -> tuple[bool, Optional[str]]:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".health_check_", suffix=".tmp", dir=str(directory))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write("health_check_probe")
            return (Path(name).read_text(encoding="utf-8") == "health_check_probe", None)
        finally:
            Path(name).unlink(missing_ok=True)
    except Exception as exc:
        return False, str(exc)


def check_skills() -> CheckResult:
    try:
        ensure_sys_path()
        loaded = importlib.import_module("agent.skills").load_all_skills()
    except Exception as exc:
        return CheckResult("Skills carregadas", STATUS_ERROR, f"Falha ao carregar skills: {exc}", {"traceback": traceback.format_exc()})
    names = [str(getattr(skill, "name", f"<sem nome: {type(skill).__name__}>")) for skill in loaded]
    missing = [name for name in ESSENTIAL_SKILLS if name not in names]
    details: Dict[str, Any] = {"loaded_skill_names": names, "total_loaded": len(names), "missing_essential_skills": missing}
    messages = [f"{len(names)} skill(s) carregada(s)."]
    status = STATUS_ERROR if missing else STATUS_OK
    if missing:
        messages.append("Essenciais ausentes: " + ", ".join(missing))
    echo = next((skill for skill in loaded if getattr(skill, "name", None) == "echo"), None)
    if echo is not None:
        try:
            result = echo.execute({"message": "health_check_ping"})
            details["echo_test_result"] = result
            if not isinstance(result, dict) or result.get("ok") is not True:
                status = STATUS_WARNING if status == STATUS_OK else status
        except Exception as exc:
            messages.append(f"Falha ao testar echo: {exc}")
            status = STATUS_WARNING if status == STATUS_OK else status
    return CheckResult("Skills carregadas", status, " ".join(messages), details)


def check_logs() -> CheckResult:
    details: Dict[str, Any] = {}
    warnings: List[str] = []
    for label, path in (("agent.log", LOG_FILE), ("agent_metrics.jsonl", METRICS_FILE)):
        if not path.exists():
            details[label] = {"exists": False}
            continue
        try:
            size = path.stat().st_size
            details[label] = {"exists": True, "size_bytes": size, "size_mb": round(size / 1024 / 1024, 2)}
            if size > LOG_SIZE_WARNING_BYTES:
                warnings.append(f"{label} está grande ({details[label]['size_mb']} MB)")
        except OSError as exc:
            details[label] = {"exists": True, "error": str(exc)}
    return CheckResult("Logs e métricas", STATUS_WARNING if warnings else STATUS_OK, "; ".join(warnings) or "Tamanhos dentro do esperado.", details)
