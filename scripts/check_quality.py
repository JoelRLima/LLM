"""Strict repository-specific quality, architecture and documentation gates."""

from __future__ import annotations

import ast
import codecs
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, cast
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "quality" / "baseline.json"
IGNORED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "build",
    "dist",
    "reports",
    "runtime",
}
COMPLEXITY_PATTERN = re.compile(r"`(?P<name>[^`]+)` is too complex \((?P<value>\d+) > (?P<limit>\d+)\)")
MARKDOWN_LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
TEXT_SUFFIXES = {".json", ".lock", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"}
TEXT_FILENAMES = {".editorconfig", ".gitattributes", ".gitignore"}


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _is_ignored(path: Path) -> bool:
    parts = path.relative_to(ROOT).parts
    return bool(IGNORED_PARTS.intersection(parts)) or any(part.endswith(".egg-info") for part in parts)


def _load_baseline() -> dict[str, object]:
    return cast(dict[str, object], json.loads(BASELINE_PATH.read_text(encoding="utf-8")))


def _ratchet_failures(
    *,
    label: str,
    current: dict[str, int],
    allowed: dict[str, int],
) -> list[str]:
    failures: list[str] = []
    for key, value in sorted(current.items()):
        limit = allowed.get(key)
        if limit is None:
            failures.append(f"{label}: nova divida em {key} ({value})")
        elif value > limit:
            failures.append(f"{label}: {key} aumentou de {limit} para {value}")
        elif value < limit:
            failures.append(f"{label}: reduza a baseline de {key} de {limit} para {value}")

    for key in sorted(set(allowed) - set(current)):
        failures.append(f"{label}: remova da baseline a entrada obsoleta {key}")
    return failures


def check_complexity(baseline: dict[str, object]) -> tuple[list[str], int]:
    config = baseline["complexity"]
    if not isinstance(config, dict):
        raise TypeError("complexity baseline must be an object")
    max_complexity = int(config["max_complexity"])
    allowed = {str(key): int(value) for key, value in dict(config["allowed"]).items()}
    command = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        ".",
        "--select",
        "C901",
        "--output-format",
        "json",
        "--config",
        f"lint.mccabe.max-complexity={max_complexity}",
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode not in {0, 1}:
        detail = result.stderr.strip() or result.stdout.strip()
        return [f"complexidade: Ruff nao executou corretamente: {detail}"], 0

    current: dict[str, int] = {}
    source_lines: dict[str, int] = {}
    for diagnostic in json.loads(result.stdout or "[]"):
        match = COMPLEXITY_PATTERN.fullmatch(diagnostic["message"])
        if match is None:
            continue
        filename = Path(diagnostic["filename"])
        if not filename.is_absolute():
            filename = ROOT / filename
        key = f"{_relative(filename)}::{match.group('name')}"
        line = int(diagnostic["location"]["row"])
        value = int(match.group("value"))
        if key in current:
            previous_line = source_lines.pop(key)
            previous_value = current.pop(key)
            current[f"{key}@{previous_line}"] = previous_value
            current[f"{key}@{line}"] = value
        else:
            current[key] = value
            source_lines[key] = line
    return _ratchet_failures(label="complexidade", current=current, allowed=allowed), len(current)


def _production_python_files() -> Iterable[Path]:
    yield from sorted((ROOT / "agent").rglob("*.py"))
    yield from sorted(ROOT.glob("*.py"))


def check_module_size(baseline: dict[str, object]) -> tuple[list[str], int]:
    config = baseline["module_size"]
    if not isinstance(config, dict):
        raise TypeError("module_size baseline must be an object")
    max_lines = int(config["max_lines"])
    allowed = {str(key): int(value) for key, value in dict(config["allowed"]).items()}
    current: dict[str, int] = {}
    for path in _production_python_files():
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > max_lines:
            current[_relative(path)] = line_count
    failures = _ratchet_failures(label="tamanho de modulo", current=current, allowed=allowed)
    return failures, len(current)


def _resolve_import(current_module: str, is_package: bool, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    module_parts = current_module.split(".")
    package = module_parts if is_package else module_parts[:-1]
    keep = max(0, len(package) - node.level + 1)
    prefix = package[:keep]
    if node.module:
        prefix.extend(node.module.split("."))
    return ".".join(prefix)


def _imports(path: Path) -> Iterable[tuple[int, str]]:
    relative = path.relative_to(ROOT).with_suffix("")
    parts = list(relative.parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    current_module = ".".join(parts)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_import(current_module, is_package, node)
            for alias in node.names:
                imported = base if alias.name == "*" else ".".join(part for part in (base, alias.name) if part)
                yield node.lineno, imported


def _matches_prefix(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in prefixes)


def _forbidden_imports(path: Path) -> tuple[str, ...]:
    relative = _relative(path)
    root_compatibility = (
        "benchmark",
        "cli",
        "cli_chat",
        "cli_streaming",
        "command_handlers",
        "command_ui",
        "commands",
        "config",
        "config_validation",
        "logger",
        "paths",
        "session",
    )
    outer_layers = (
        "agent.interfaces",
        "agent.orchestrator",
        "agent.skills",
        "cli",
        "commands",
        "session",
    )
    if relative.startswith("agent/code/"):
        return root_compatibility + outer_layers + ("agent.llm.providers",)
    if relative.startswith("agent/runtime/"):
        return root_compatibility + outer_layers + ("agent.code", "agent.planning", "agent.llm.providers")
    if relative.startswith("agent/evaluation/"):
        return root_compatibility + outer_layers + ("agent.code", "agent.llm.providers")
    stable_llm_core = relative in {
        "agent/llm/contracts.py",
        "agent/llm/structured_output.py",
    } or relative.startswith("agent/llm/providers/")
    if stable_llm_core:
        return root_compatibility + outer_layers + ("agent.code", "agent.planning")
    if relative in {
        "agent/planning/task_graph.py",
        "agent/planning/task_scheduler.py",
    }:
        return root_compatibility + outer_layers + ("agent.llm.providers",)
    return root_compatibility


def check_architecture() -> tuple[list[str], int]:
    failures: list[str] = []
    checked = 0
    for path in sorted((ROOT / "agent").rglob("*.py")):
        forbidden = _forbidden_imports(path)
        if not forbidden:
            continue
        checked += 1
        for line, imported in _imports(path):
            if _matches_prefix(imported, forbidden):
                failures.append(
                    f"arquitetura: {_relative(path)}:{line} importa camada proibida {imported}"
                )
    return failures, checked


def _markdown_files() -> Iterable[Path]:
    for path in sorted(ROOT.rglob("*.md")):
        if not _is_ignored(path):
            yield path


def check_markdown_links() -> tuple[list[str], int]:
    failures: list[str] = []
    checked = 0
    for path in _markdown_files():
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for match in MARKDOWN_LINK_PATTERN.finditer(line):
                raw_target = match.group(1).strip()
                target = (
                    raw_target[1 : raw_target.find(">")]
                    if raw_target.startswith("<") and ">" in raw_target
                    else raw_target.split()[0]
                )
                if not target or target.startswith("#") or "://" in target or target.startswith("mailto:"):
                    continue
                checked += 1
                local_target = unquote(target.split("#", 1)[0])
                resolved = (ROOT / local_target.lstrip("/")) if local_target.startswith("/") else (path.parent / local_target)
                if not resolved.exists():
                    failures.append(
                        f"documentacao: {_relative(path)}:{line_number} aponta para {target}, que nao existe"
                    )
    return failures, checked


def check_text_encoding() -> tuple[list[str], int]:
    """Require repository text artifacts to be valid BOM-free UTF-8."""
    failures: list[str] = []
    checked = 0
    for path in sorted(ROOT.rglob("*")):
        if _is_ignored(path):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in TEXT_FILENAMES:
            continue
        if not path.is_file():
            continue
        checked += 1
        content = path.read_bytes()
        if content.startswith((codecs.BOM_UTF8, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            failures.append(f"encoding: {_relative(path)} deve usar UTF-8 sem BOM")
            continue
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            failures.append(f"encoding: {_relative(path)} nao e UTF-8 valido ({exc})")
    return failures, checked


def run_checks() -> tuple[list[str], dict[str, int]]:
    baseline = _load_baseline()
    failures: list[str] = []
    counts: dict[str, int] = {}
    for name, check in (
        ("complexity_debt", lambda: check_complexity(baseline)),
        ("oversized_modules", lambda: check_module_size(baseline)),
        ("architecture_modules", check_architecture),
        ("local_doc_links", check_markdown_links),
        ("utf8_text_files", check_text_encoding),
    ):
        check_failures, count = check()
        failures.extend(check_failures)
        counts[name] = count
    return failures, counts


def main() -> int:
    failures, counts = run_checks()
    if failures:
        print("Quality gates failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    summary = ", ".join(f"{name}={count}" for name, count in counts.items())
    print(f"Quality gates passed ({summary}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
