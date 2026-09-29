from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from llm_agent.agent.health.core import (
    EXPECTED_MEMORY_SECTIONS,
    MEMORY_BACKUP_DIR,
    MEMORY_PATH,
    REQUIRED_CONFIG_KEYS,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_WARNING,
    CheckResult,
    HealthPathContext,
    resolve_health_paths,
)
from llm_agent.agent.runtime.config import carregar_config


def _selected_paths(
    *,
    app_paths: Any | None = None,
    workspace_paths: Any | None = None,
    workspace_root: str | Path | None = None,
) -> HealthPathContext:
    if app_paths is None and workspace_paths is None and workspace_root is None:
        legacy = resolve_health_paths()
        return HealthPathContext(
            config_file=legacy.config_file,
            memory_file=MEMORY_PATH,
            memory_backup_dir=MEMORY_BACKUP_DIR,
            restore_points_dir=legacy.restore_points_dir,
            temp_analysis_dir=legacy.temp_analysis_dir,
            log_file=legacy.log_file,
            metrics_file=legacy.metrics_file,
            health_report_file=legacy.health_report_file,
            project_root=legacy.project_root,
        )
    return resolve_health_paths(
        app_paths=app_paths,
        workspace_paths=workspace_paths,
        workspace_root=workspace_root,
    )


def _has_context(
    app_paths: Any | None,
    workspace_paths: Any | None,
    workspace_root: str | Path | None,
) -> bool:
    return app_paths is not None or workspace_paths is not None or workspace_root is not None


def check_python_version() -> CheckResult:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    valid = sys.version_info[:2] >= (3, 10)
    status = STATUS_OK if valid else STATUS_ERROR
    relation = "atende" if valid else "não atende"
    return CheckResult("Versão do Python", status, f"Python {version} {relation} ao mínimo 3.10.", {"version": version})


def check_config(
    *,
    app_paths: Any | None = None,
    workspace_paths: Any | None = None,
    workspace_root: str | Path | None = None,
) -> CheckResult:
    selected = _selected_paths(
        app_paths=app_paths,
        workspace_paths=workspace_paths,
        workspace_root=workspace_root,
    )
    config_path = selected.config_file
    details: Dict[str, Any] = {"path": str(config_path)}
    if not config_path.exists():
        return CheckResult("Configuração (config.json)", STATUS_ERROR, "Arquivo config.json não encontrado.", details)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult("Configuração (config.json)", STATUS_ERROR, f"Configuração inválida: {exc}", details)
    missing = [key for key in REQUIRED_CONFIG_KEYS if key not in raw]
    details.update({"missing_keys": missing, "present_keys": list(raw)})
    try:
        carregar_config(str(config_path), workspace_paths=workspace_paths)
        details["loaded_ok"] = True
    except Exception as exc:
        details.update({"loaded_ok": False, "load_error": str(exc)})
        return CheckResult("Configuração (config.json)", STATUS_ERROR, f"carregar_config falhou: {exc}", details)
    if missing:
        return CheckResult("Configuração (config.json)", STATUS_WARNING, f"Faltam chaves com fallback: {', '.join(missing)}.", details)
    return CheckResult("Configuração (config.json)", STATUS_OK, "Arquivo de configuração válido e completo.", details)


def check_memory(
    *,
    app_paths: Any | None = None,
    workspace_paths: Any | None = None,
    workspace_root: str | Path | None = None,
) -> CheckResult:
    selected = _selected_paths(
        app_paths=app_paths,
        workspace_paths=workspace_paths,
        workspace_root=workspace_root,
    )
    memory_path = selected.memory_file
    details: Dict[str, Any] = {"path": str(memory_path)}
    if not memory_path.exists():
        return CheckResult("Memória (agent_memory.json)", STATUS_WARNING, "Memória persistente ainda não existe.", details)
    try:
        data = json.loads(memory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult("Memória (agent_memory.json)", STATUS_ERROR, f"Memória inválida: {exc}", details)
    missing = [section for section in EXPECTED_MEMORY_SECTIONS if section not in data]
    backups = (
        check_memory_backups(
            app_paths=app_paths,
            workspace_paths=workspace_paths,
            workspace_root=workspace_root,
        )
        if _has_context(app_paths, workspace_paths, workspace_root)
        else check_memory_backups()
    )
    details.update({"missing_sections": missing, "present_sections": list(data), "backups": backups})
    warnings = []
    if missing:
        warnings.append("seções ausentes: " + ", ".join(missing))
    if backups["invalid_files"]:
        warnings.append(f"{len(backups['invalid_files'])} backups inválidos")
    status = STATUS_WARNING if warnings else STATUS_OK
    message = "; ".join(warnings) if warnings else "Memória válida e com as seções esperadas."
    return CheckResult("Memória (agent_memory.json)", status, message, details)


def check_memory_backups(
    *,
    app_paths: Any | None = None,
    workspace_paths: Any | None = None,
    workspace_root: str | Path | None = None,
) -> Dict[str, Any]:
    selected = _selected_paths(
        app_paths=app_paths,
        workspace_paths=workspace_paths,
        workspace_root=workspace_root,
    )
    backup_dir = selected.memory_backup_dir
    info: Dict[str, Any] = {"dir_exists": backup_dir.exists(), "total_backups": 0, "valid_files": [], "invalid_files": []}
    if not backup_dir.exists():
        return info
    try:
        backups = sorted(path for path in backup_dir.iterdir() if path.is_file() and path.suffix == ".bak")
    except OSError as exc:
        info["error"] = str(exc)
        return info
    info["total_backups"] = len(backups)
    for backup in backups:
        try:
            json.loads(backup.read_text(encoding="utf-8"))
            info["valid_files"].append(backup.name)
        except (OSError, json.JSONDecodeError) as exc:
            info["invalid_files"].append({"file": backup.name, "error": str(exc)})
    return info


def check_file_hashes(
    *,
    app_paths: Any | None = None,
    workspace_paths: Any | None = None,
    workspace_root: str | Path | None = None,
) -> CheckResult:
    selected = _selected_paths(
        app_paths=app_paths,
        workspace_paths=workspace_paths,
        workspace_root=workspace_root,
    )
    memory_path = selected.memory_file
    if not memory_path.exists():
        return CheckResult("Hashes de arquivos", STATUS_WARNING, "Sem memória para verificar hashes.")
    try:
        hashes = json.loads(memory_path.read_text(encoding="utf-8")).get("file_hashes", {})
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult("Hashes de arquivos", STATUS_ERROR, f"Não foi possível ler hashes: {exc}")
    if not hashes:
        return CheckResult("Hashes de arquivos", STATUS_OK, "Nenhum hash registrado.")
    matched: List[str] = []
    mismatched: List[Dict[str, str]] = []
    missing: List[str] = []
    for relative, expected in hashes.items():
        target = selected.project_root / relative
        if not target.exists():
            missing.append(relative)
        elif sha256_of_file(target) == expected:
            matched.append(relative)
        else:
            mismatched.append({"file": relative, "expected": expected, "actual": sha256_of_file(target)})
    details = {"matched": matched, "mismatched": mismatched, "missing_files": missing}
    if mismatched or missing:
        return CheckResult("Hashes de arquivos", STATUS_WARNING, f"{len(mismatched)} divergentes e {len(missing)} ausentes.", details)
    return CheckResult("Hashes de arquivos", STATUS_OK, f"Todos os {len(matched)} hashes conferem.", details)


def sha256_of_file(path: Path, chunk_size: int = 65536) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
