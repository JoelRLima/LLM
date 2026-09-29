"""Validate the closed W22 production process-launch disposition matrix."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "src" / "llm_agent"
MATRIX_PATH = ROOT / "quality" / "process_w22_dispositions.json"
ALLOWED = {
    "MIGRATED_TO_COMMAND_EXECUTOR",
    "PLATFORM_SESSION_PRIMITIVE",
    "SPECIALIZED_RETAINED",
    "NON_PRODUCTION/OUTSIDE_W22",
}
DIRECT_LAUNCHERS = {
    "subprocess.Popen",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "os.system",
    "multiprocessing.Process",
}
CONSTRUCTOR_NAMES = {"subprocess.CompletedProcess", "subprocess.TimeoutExpired", "subprocess.CalledProcessError"}


def _qualified(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _module(path: Path) -> str:
    relative = path.relative_to(SOURCE_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return "llm_agent." + ".".join(parts)


def _direct_launchers() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call = _qualified(node.func)
            if call in DIRECT_LAUNCHERS and call not in CONSTRUCTOR_NAMES:
                found.add((_module(path), call))
    return found


def _matrix() -> dict[str, Any]:
    value = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("process disposition matrix must be an object")
    return value


def _record_errors(records: list[Any]) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, record in enumerate(records):
        label = f"dispositions[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{label} must be an object")
            continue
        module, launcher = record.get("module"), record.get("launcher")
        if not isinstance(module, str) or not isinstance(launcher, str):
            errors.append(f"{label} requires module and launcher strings")
            continue
        key = (module, launcher)
        if key in seen:
            errors.append(f"duplicate process disposition: {module}:{launcher}")
        seen.add(key)
        if record.get("disposition") not in ALLOWED:
            errors.append(f"{label} has unsupported disposition: {record.get('disposition')!r}")
        evidence = record.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            errors.append(f"{label} requires evidence")
        elif not (ROOT / evidence).is_file():
            errors.append(f"{label} evidence file is missing: {evidence}")
    return errors


def _launcher_matrix_errors(records: list[Any]) -> list[str]:
    errors: list[str] = []
    direct = _direct_launchers()
    matrix_direct = {
        (str(record.get("module")), str(record.get("launcher")))
        for record in records
        if isinstance(record, dict) and str(record.get("launcher")) in DIRECT_LAUNCHERS
    }
    for module, launcher in sorted(direct - matrix_direct):
        errors.append(f"unclassified production launcher: {module}:{launcher}")
    for module, launcher in sorted(matrix_direct - direct):
        errors.append(f"stale direct launcher disposition: {module}:{launcher}")
    return errors


def _adapter_errors(records: list[Any]) -> list[str]:
    errors: list[str] = []
    required_adapters = {
        "llm_agent.application.services.query_git",
        "llm_agent.agent.code.validation_process",
        "llm_agent.agent.skills.shell_process",
        "llm_agent.agent.skills.python_process",
        "llm_agent.agent.engineering.backends.repository",
        "llm_agent.agent.evaluation.evaluation_identity",
        "llm_agent.agent.tools.stdio_process",
    }
    for module in sorted(required_adapters):
        path = SOURCE_ROOT.joinpath(*module.split(".")[1:]).with_suffix(".py")
        source = path.read_text(encoding="utf-8") if path.is_file() else ""
        declared = {
            str(record.get("disposition"))
            for record in records
            if isinstance(record, dict) and record.get("module") == module
        }
        if "CommandExecutor" not in source or "MIGRATED_TO_COMMAND_EXECUTOR" not in declared:
            errors.append(f"CommandExecutor adapter is not evidenced for {module}")
    return errors


def validate() -> list[str]:
    document = _matrix()
    errors: list[str] = []
    if document.get("status") != "CLOSED":
        errors.append("process disposition matrix is not CLOSED")
    records = document.get("dispositions")
    if not isinstance(records, list):
        return ["process disposition matrix must contain a dispositions list"]
    errors.extend(_record_errors(records))
    errors.extend(_launcher_matrix_errors(records))
    errors.extend(_adapter_errors(records))
    return sorted(set(errors))


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    direct = sorted(f"{module}:{launcher}" for module, launcher in _direct_launchers())
    print("W22 process dispositions: PASS")
    print(f"classified direct launchers: {len(direct)}")
    for item in direct:
        print(f"  {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
