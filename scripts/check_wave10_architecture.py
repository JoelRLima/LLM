"""Small source-only ownership checks for supported task continuity."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class ArchitectureViolation:
    rule_id: str
    path: str
    detail: str
    line: int | None = None

    def format(self) -> str:
        suffix = f":{self.line}" if self.line is not None else ""
        return f"{self.rule_id} {self.path}{suffix}: {self.detail}"


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _tree(root: Path, relative: str) -> ast.Module | None:
    path = root / relative
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    except (OSError, SyntaxError, UnicodeError):
        return None


def _violation(rule_id: str, relative: str, detail: str, node: ast.AST | None = None) -> ArchitectureViolation:
    return ArchitectureViolation(rule_id, relative, detail, getattr(node, "lineno", None))


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _function(tree: ast.Module | None, name: str) -> ast.FunctionDef | None:
    if tree is None:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _has_explicit_resume_route(function: ast.FunctionDef) -> bool:
    names = {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}
    has_application = bool({"_create_application", "create_application"} & names)
    has_explicit_flag = any(
        isinstance(node, ast.keyword) and node.arg == "explicit_resume"
        for node in ast.walk(function)
    )
    has_resume_method = any(
        isinstance(node, ast.Attribute) and node.attr == "resume"
        for node in ast.walk(function)
    )
    return has_application and (has_explicit_flag or has_resume_method)


def _check_cli_imports(tree: ast.Module, relative: str) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        module = (node.module or "") if isinstance(node, ast.ImportFrom) else (node.names[0].name if node.names else "")
        if module in {"agent.state", "agent.tool_executor"}:
            violations.append(_violation("W10-S3", relative, "CLI imports an execution/state owner directly", node))
    return violations


def _dispatches_task_adapter(dispatch: ast.FunctionDef | None) -> bool:
    if dispatch is None:
        return False
    return any(
        isinstance(node, ast.Call) and _call_name(node) == "dispatch_task"
        for node in ast.walk(dispatch)
    )


def _check_status_boundary(
    tree: ast.Module,
    adapter_tree: ast.Module | None,
    relative: str,
    delegates: bool,
) -> list[ArchitectureViolation]:
    status = _function(tree, "_run_task_status")
    if status is None:
        status = _function(adapter_tree, "run_task_status")
        if not delegates:
            status = None
    if status is None:
        return [_violation("W10-S3", relative, "task status handler is missing")]
    violations: list[ArchitectureViolation] = []
    for node in ast.walk(status):
        if isinstance(node, ast.Call) and _call_name(node) in {"_create_application", "run", "resume", "execute"}:
            violations.append(
                _violation("W10-S3", relative, "task status constructs or invokes an execution owner", node)
            )
    return violations


def _check_resume_boundary(
    tree: ast.Module,
    adapter_tree: ast.Module | None,
    relative: str,
    delegates: bool,
) -> list[ArchitectureViolation]:
    resume = _function(tree, "_run_task_resume")
    if resume is None:
        resume = _function(adapter_tree, "run_task_resume")
        if resume is None or not delegates:
            return [_violation("W10-S4", relative, "task resume handler is missing")]
        return [] if _has_explicit_resume_route(resume) else [
            _violation("W10-S4", relative, "task resume does not route through the explicit application boundary")
        ]
    if _has_explicit_resume_route(resume):
        return []
    adapter = _function(adapter_tree, "run_task_resume")
    delegates_to_adapter = any(
        isinstance(node, ast.Call) and _call_name(node) == "run_task_resume"
        for node in ast.walk(resume)
    )
    if adapter is not None and delegates_to_adapter and _has_explicit_resume_route(adapter):
        return []
    return [
        _violation("W10-S4", relative, "task resume does not route through the explicit application boundary")
    ]


def _check_service_filesystem(
    tree: ast.Module,
    relative: str,
) -> list[ArchitectureViolation]:
    forbidden_modules = frozenset({"os", "pathlib", "shutil", "tempfile"})
    violations: list[ArchitectureViolation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in forbidden_modules:
                    violations.append(
                        _violation("W10-S1", relative, "continuity service imports filesystem primitives", node)
                    )
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".", 1)[0] in forbidden_modules:
            violations.append(
                _violation("W10-S1", relative, "continuity service imports filesystem primitives", node)
            )
        elif isinstance(node, ast.Call) and _call_name(node) in {
            "open",
            "mkdir",
            "unlink",
            "remove",
            "replace",
            "rename",
            "write_text",
            "write_bytes",
        }:
            violations.append(
                _violation("W10-S1", relative, "continuity service performs checkpoint filesystem I/O", node)
            )
    return violations


def _check_service_observability(
    tree: ast.Module,
    relative: str,
) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = (
                (node.module or "")
                if isinstance(node, ast.ImportFrom)
                else (node.names[0].name if node.names else "")
            )
            if module.startswith("agent.observability"):
                violations.append(
                    _violation("W10-S2", relative, "continuity correctness depends on observability trace code", node)
                )
        if isinstance(node, ast.Name) and node.id in {"TraceStore", "TraceCatalog", "TraceReaderMixin"}:
            violations.append(
                _violation("W10-S2", relative, "continuity correctness depends on observability trace code", node)
            )
    return violations


def _check_continuity_service(root: Path) -> list[ArchitectureViolation]:
    relative = "agent/continuity/service.py"
    tree = _tree(root, relative)
    if tree is None:
        return [_violation("W10-S1", relative, "continuity service is missing or unparsable")]
    return _check_service_filesystem(tree, relative) + _check_service_observability(tree, relative)


def _check_cli_boundaries(root: Path) -> list[ArchitectureViolation]:
    relative = "agent/interfaces/cli/app.py"
    tree = _tree(root, relative)
    if tree is None:
        return [_violation("W10-S3", relative, "continuity CLI adapter is missing or unparsable")]
    adapter_tree = _tree(root, "agent/interfaces/cli/task_continuity.py")
    delegates = _dispatches_task_adapter(_function(tree, "_dispatch_task"))
    return (
        _check_cli_imports(tree, relative)
        + _check_status_boundary(tree, adapter_tree, relative, delegates)
        + _check_resume_boundary(tree, adapter_tree, relative, delegates)
    )


def _check_task_runner_boundary(root: Path) -> list[ArchitectureViolation]:
    relative = "agent/orchestration/task_runner.py"
    tree = _tree(root, relative)
    if tree is None:
        return [_violation("W10-S5", relative, "TaskRunner is missing or unparsable")]
    source = (root / relative).read_text(encoding="utf-8")
    violations: list[ArchitectureViolation] = []
    if "explicit_resume" not in source or "_resolve_inputs" not in source:
        violations.append(
            _violation("W10-S5", relative, "explicit resume does not use the canonical TaskRunner restore route")
        )
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = (
                (node.module or "")
                if isinstance(node, ast.ImportFrom)
                else (node.names[0].name if node.names else "")
            )
            if module.startswith("agent.observability"):
                violations.append(
                    _violation("W10-S6", relative, "TaskRunner uses observability traces as resume authority", node)
                )
    continuity_root = root / "agent" / "continuity"
    if continuity_root.exists():
        for path in continuity_root.glob("*.py"):
            if path.stem.casefold() in {"catalog", "daemon", "scheduler", "service_runner"}:
                violations.append(
                    _violation("W10-S7", _relative(path, root), "continuity introduces a deferred multi-task/service owner")
                )
    return violations


def check_source(path: str | Path, root: str | Path | None = None) -> list[ArchitectureViolation]:
    resolved_root = Path(root).expanduser().resolve() if root is not None else ROOT
    source = Path(path).expanduser().resolve()
    try:
        relative = _relative(source, resolved_root)
    except ValueError:
        return [_violation("W10-S0", str(source), "source is outside repository root")]
    return [item for item in check_architecture(resolved_root) if item.path == relative]


def check_architecture(root: str | Path = ".") -> list[ArchitectureViolation]:
    resolved = Path(root).expanduser().resolve()
    return [
        finding
        for check in (_check_continuity_service, _check_cli_boundaries, _check_task_runner_boundary)
        for finding in check(resolved)
    ]


find_violations = check_architecture
check_wave10_architecture = check_architecture


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check continuity ownership boundaries")
    parser.add_argument("root", nargs="?", default=".", help="repository root")
    args = parser.parse_args(list(argv) if argv is not None else None)
    violations = check_architecture(args.root)
    if violations:
        for violation in violations:
            print(violation.format())
        return 1
    print("W10 architecture checks: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
