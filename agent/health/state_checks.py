from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from agent.health.core import (
    CONFIG_PATH,
    EXPECTED_MEMORY_SECTIONS,
    MEMORY_BACKUP_DIR,
    MEMORY_PATH,
    PROJECT_ROOT,
    REQUIRED_CONFIG_KEYS,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_WARNING,
    CheckResult,
    ensure_sys_path,
)


def check_python_version() -> CheckResult:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    valid = sys.version_info[:2] >= (3, 10)
    status = STATUS_OK if valid else STATUS_ERROR
    relation = "atende" if valid else "não atende"
    return CheckResult("Versão do Python", status, f"Python {version} {relation} ao mínimo 3.10.", {"version": version})


def check_config() -> CheckResult:
    details: Dict[str, Any] = {"path": str(CONFIG_PATH)}
    if not CONFIG_PATH.exists():
        return CheckResult("Configuração (config.json)", STATUS_ERROR, "Arquivo config.json não encontrado.", details)
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult("Configuração (config.json)", STATUS_ERROR, f"Configuração inválida: {exc}", details)
    missing = [key for key in REQUIRED_CONFIG_KEYS if key not in raw]
    details.update({"missing_keys": missing, "present_keys": list(raw)})
    try:
        ensure_sys_path()
        importlib.import_module("config").carregar_config(str(CONFIG_PATH))
        details["loaded_ok"] = True
    except Exception as exc:
        details.update({"loaded_ok": False, "load_error": str(exc)})
        return CheckResult("Configuração (config.json)", STATUS_ERROR, f"carregar_config falhou: {exc}", details)
    if missing:
        return CheckResult("Configuração (config.json)", STATUS_WARNING, f"Faltam chaves com fallback: {', '.join(missing)}.", details)
    return CheckResult("Configuração (config.json)", STATUS_OK, "Arquivo de configuração válido e completo.", details)


def check_memory() -> CheckResult:
    details: Dict[str, Any] = {"path": str(MEMORY_PATH)}
    if not MEMORY_PATH.exists():
        return CheckResult("Memória (agent_memory.json)", STATUS_WARNING, "Memória persistente ainda não existe.", details)
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult("Memória (agent_memory.json)", STATUS_ERROR, f"Memória inválida: {exc}", details)
    missing = [section for section in EXPECTED_MEMORY_SECTIONS if section not in data]
    backups = check_memory_backups()
    details.update({"missing_sections": missing, "present_sections": list(data), "backups": backups})
    warnings = []
    if missing:
        warnings.append("seções ausentes: " + ", ".join(missing))
    if backups["invalid_files"]:
        warnings.append(f"{len(backups['invalid_files'])} backups inválidos")
    status = STATUS_WARNING if warnings else STATUS_OK
    message = "; ".join(warnings) if warnings else "Memória válida e com as seções esperadas."
    return CheckResult("Memória (agent_memory.json)", status, message, details)


def check_memory_backups() -> Dict[str, Any]:
    info: Dict[str, Any] = {"dir_exists": MEMORY_BACKUP_DIR.exists(), "total_backups": 0, "valid_files": [], "invalid_files": []}
    if not MEMORY_BACKUP_DIR.exists():
        return info
    try:
        backups = sorted(path for path in MEMORY_BACKUP_DIR.iterdir() if path.is_file() and path.suffix == ".bak")
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


def check_file_hashes() -> CheckResult:
    if not MEMORY_PATH.exists():
        return CheckResult("Hashes de arquivos", STATUS_WARNING, "Sem memória para verificar hashes.")
    try:
        hashes = json.loads(MEMORY_PATH.read_text(encoding="utf-8")).get("file_hashes", {})
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult("Hashes de arquivos", STATUS_ERROR, f"Não foi possível ler hashes: {exc}")
    if not hashes:
        return CheckResult("Hashes de arquivos", STATUS_OK, "Nenhum hash registrado.")
    matched: List[str] = []
    mismatched: List[Dict[str, str]] = []
    missing: List[str] = []
    for relative, expected in hashes.items():
        target = PROJECT_ROOT / relative
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
