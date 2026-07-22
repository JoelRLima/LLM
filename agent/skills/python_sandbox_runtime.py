"""Runtime helpers for the ephemeral Python sandbox."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from agent.skills.python_sandbox_policy import ALLOWED_BUILTINS


@dataclass(frozen=True)
class SandboxLimits:
    max_files: int = 20
    max_directories: int = 10
    max_depth: int = 5
    max_file_size: int = 2 * 1024 * 1024
    max_total_size: int = 5 * 1024 * 1024


def build_wrapper(code: str) -> str:
    allowed_repr = repr(sorted(ALLOWED_BUILTINS))
    return (
        "import builtins as _builtins\n"
        f"_allowed_names = {allowed_repr}\n"
        "_restricted = {n: getattr(_builtins, n) for n in _allowed_names if hasattr(_builtins, n)}\n"
        f"_user_code = {code!r}\n"
        "_globals = {'__builtins__': _restricted, '__name__': '__main__'}\n"
        "exec(compile(_user_code, '<agent_code>', 'exec'), _globals)\n"
    )


def _is_reparse_point(path: str) -> bool:
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", None)
    except OSError:
        return False
    return bool(attributes is not None and attributes & 0x400)


def inspect_sandbox(temp_dir: str) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "file_count": 0,
        "dir_count": 0,
        "max_depth": 0,
        "total_size": 0,
        "max_file_size": 0,
        "suspicious": [],
    }
    suspicious: list[str] = stats["suspicious"]
    for dirpath, dirnames, filenames in os.walk(temp_dir, followlinks=False):
        relative = os.path.relpath(dirpath, temp_dir)
        depth = 0 if relative == "." else relative.count(os.sep) + 1
        stats["max_depth"] = max(stats["max_depth"], depth)
        for dirname in dirnames:
            full_path = os.path.join(dirpath, dirname)
            stats["dir_count"] += 1
            if os.path.islink(full_path) or _is_reparse_point(full_path):
                suspicious.append(f"link/reparse point (diretório): {dirname}")
        for filename in filenames:
            full_path = os.path.join(dirpath, filename)
            stats["file_count"] += 1
            if os.path.islink(full_path) or _is_reparse_point(full_path):
                suspicious.append(f"link/reparse point (arquivo): {filename}")
            try:
                size = os.path.getsize(full_path)
            except OSError:
                size = 0
            stats["total_size"] += size
            stats["max_file_size"] = max(stats["max_file_size"], size)
    return stats


def validate_sandbox_state(temp_dir: str, limits: SandboxLimits) -> str | None:
    stats = inspect_sandbox(temp_dir)
    file_count = max(0, int(stats["file_count"]) - 1)
    suspicious = stats["suspicious"]
    if suspicious:
        return "Estruturas não permitidas detectadas: " + "; ".join(suspicious[:5])
    if file_count > limits.max_files:
        return f"Quantidade de arquivos criados excede o limite ({file_count} > {limits.max_files})"
    if stats["dir_count"] > limits.max_directories:
        return f"Quantidade de diretórios criados excede o limite ({stats['dir_count']} > {limits.max_directories})"
    if stats["max_depth"] > limits.max_depth:
        return f"Profundidade da árvore excede o limite ({stats['max_depth']} > {limits.max_depth})"
    if stats["max_file_size"] > limits.max_file_size:
        return f"Arquivo excede o tamanho máximo permitido ({stats['max_file_size']} > {limits.max_file_size} bytes)"
    if stats["total_size"] > limits.max_total_size:
        return f"Tamanho total da sandbox excede o limite ({stats['total_size']} > {limits.max_total_size} bytes)"
    return None
