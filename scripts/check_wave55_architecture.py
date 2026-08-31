"""Scoped structural checks for the task-authority task-authority boundary.

The checker intentionally inspects only the files and symbols that form the
task-authority seam.  It is not a repository-wide style or naming blacklist.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ArchitectureViolation:
    rule_id: str
    path: str
    detail: str
    line: int | None = None

    def format(self) -> str:
        suffix = f":{self.line}" if self.line is not None else ""
        return f"{self.rule_id} {self.path}{suffix}: {self.detail}"

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "path": self.path,
            "detail": self.detail,
            "line": self.line,
        }


def _source(root: Path, relative: str) -> str | None:
    path = root / relative
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _tree(root: Path, relative: str) -> ast.AST | None:
    source = _source(root, relative)
    if source is None:
        return None
    try:
        return ast.parse(source, filename=relative)
    except SyntaxError:
        return None


def _violation(rule_id: str, relative: str, detail: str, line: int | None = None) -> ArchitectureViolation:
    return ArchitectureViolation(rule_id, relative, detail, line)


def _names(tree: ast.AST) -> set[str]:
    names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    }
    names.update(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )
    names.update(
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    )
    return names


def _attribute_names(tree: ast.AST) -> set[str]:
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }


def _function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _call_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        target = call.func
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def _compiler_absence_test(test: ast.AST) -> bool:
    """Recognize the compiler-missing branch without scanning arbitrary text."""

    return any(
        isinstance(node, ast.Compare)
        and any(
            isinstance(value, ast.Name)
            and value.id == "compiler"
            for value in ast.walk(node.left)
        )
        and any(
            isinstance(value, ast.Constant) and value.value is None
            for value in ast.walk(node)
        )
        and any(isinstance(operator, (ast.Is, ast.Eq)) for operator in node.ops)
        for node in ast.walk(test)
    )


def _returns_none_in_branch(nodes: list[ast.stmt]) -> ast.Return | None:
    for statement in nodes:
        for node in ast.walk(statement):
            if isinstance(node, ast.Return) and node.value is None:
                return node
    return None


def _has_return_in_branch(nodes: list[ast.stmt]) -> bool:
    return any(isinstance(node, ast.Return) for statement in nodes for node in ast.walk(statement))


def _contains_name(node: ast.AST, name: str) -> bool:
    return any(isinstance(item, ast.Name) and item.id == name for item in ast.walk(node))


def _ensure_result_bindings(function: ast.AST) -> set[str]:
    bindings: set[str] = set()
    for node in ast.walk(function):
        value: ast.AST | None = None
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            value = node.value
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            value = node.value
            targets = [node.target]
        if not isinstance(value, ast.Call):
            continue
        if not (
            isinstance(value.func, ast.Attribute)
            and value.func.attr == "_ensure_task_definition"
        ):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                bindings.add(target.id)
    return bindings


def _s6_gate_shape_violation(
    tree: ast.AST,
    helper_tree: ast.AST | None,
    source: str,
    helper_source: str,
    relative: str,
) -> ArchitectureViolation | None:
    required = (
        _function(tree, '_ensure_task_definition'),
        _function(helper_tree, 'ensure_task_definition') if helper_tree is not None else None,
        _function(tree, 'run'),
        _function(tree, '_execute'),
    )
    if any(item is None for item in required) or 'task_definition_compiler' not in source + helper_source:
        return _violation('W55-S6', relative, 'runner lacks the task-definition admission gate')
    return None


def _s6_compiler_absence_violations(
    gate: ast.AST,
    helper_relative: str,
) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for node in ast.walk(gate):
        if not isinstance(node, ast.If) or not _compiler_absence_test(node.test):
            continue
        none_return = _returns_none_in_branch(node.body)
        if none_return is not None:
            violations.append(
                _violation(
                    'W55-S6',
                    helper_relative,
                    'compiler absence returns normally instead of failing closed',
                    none_return.lineno,
                )
            )
    return violations


def _s6_gate_result_guard(node: ast.AST, bindings: set[str]) -> bool:
    if not isinstance(node, ast.If):
        return False
    if not any(_contains_name(node.test, binding) for binding in bindings):
        return False
    return _has_return_in_branch(node.body) or _has_return_in_branch(node.orelse)


def _s6_result_violations(
    run: ast.FunctionDef | ast.AsyncFunctionDef,
    relative: str,
) -> list[ArchitectureViolation]:
    bindings = _ensure_result_bindings(run)
    if not bindings:
        return [
            _violation(
                'W55-S6',
                relative,
                'run discards the admission-gate result before execution',
                run.lineno,
            )
        ]
    if any(_s6_gate_result_guard(node, bindings) for node in ast.walk(run)):
        return []
    return [
        _violation(
            'W55-S6',
            relative,
            'run does not branch on the admission-gate result before execution',
            run.lineno,
        )
    ]


def _call_lines(function: ast.AST, name: str) -> list[int]:
    return [
        call.lineno
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == name
    ]


def _s6_order_violations(
    run: ast.FunctionDef | ast.AsyncFunctionDef,
    relative: str,
) -> list[ArchitectureViolation]:
    ensure_lines = _call_lines(run, '_ensure_task_definition')
    execute_lines = _call_lines(run, '_execute')
    violations: list[ArchitectureViolation] = []
    if not ensure_lines:
        violations.append(_violation('W55-S6', relative, 'run does not invoke the admission gate', run.lineno))
    if execute_lines and ensure_lines and min(ensure_lines) >= min(execute_lines):
        violations.append(_violation('W55-S6', relative, 'admission gate is not before execution', run.lineno))
    return violations


def _has_symbol_reference(tree: ast.AST, name: str) -> bool:
    return any(
        (isinstance(node, ast.Name) and node.id == name)
        or (isinstance(node, ast.Attribute) and node.attr == name)
        for node in ast.walk(tree)
    )

def _check_s1(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    authority_relative = "agent/tools/authority.py"
    task_root = root / "agent" / "task_definition"
    for path in (root / "agent").rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        tree = _tree(root, relative)
        if tree is None:
            continue
        if relative != authority_relative:
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == "TaskAuthoritySnapshot":
                    violations.append(
                        _violation("W55-S1", relative, "TaskAuthoritySnapshot has a second owner", node.lineno)
                    )
    if task_root.exists():
        for path in task_root.rglob("*.py"):
            relative = path.relative_to(root).as_posix()
            tree = _tree(root, relative)
            if tree is not None and _has_symbol_reference(tree, 'TaskAuthoritySnapshot'):
                violations.append(
                    _violation("W55-S1", relative, "task-definition authority imports capability authority")
                )
    return violations


def _check_s2(root: Path) -> list[ArchitectureViolation]:
    relative = "agent/task_definition/repository.py"
    tree = _tree(root, relative)
    if tree is None:
        return [_violation("W55-S2", relative, "repository.py is missing or unparsable")]
    names = _names(tree)
    attrs = _attribute_names(tree)
    violations: list[ArchitectureViolation] = []
    for required in ("WorkspacePaths", "write_json_atomic", "reject_link_like"):
        if required not in names:
            violations.append(_violation("W55-S2", relative, f"missing canonical persistence seam: {required}"))
    if "task_definitions_dir" not in attrs:
        violations.append(_violation("W55-S2", relative, "repository does not use WorkspacePaths.task_definitions_dir"))
    forbidden = {"memory_file", "memory_db_file", "checkpoint_file", "cache_dir"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden:
            violations.append(_violation("W55-S2", relative, f"canonical repository uses forbidden path: {node.attr}", node.lineno))
    return violations


def _check_s3(root: Path) -> list[ArchitectureViolation]:
    relative = "agent/state_checkpointing.py"
    tree = _tree(root, relative)
    if tree is None:
        return [_violation("W55-S3", relative, "checkpoint projection is missing or unparsable")]
    names = _names(tree)
    violations: list[ArchitectureViolation] = []
    if "TaskDefinitionRef" not in names or "task_definition_ref" not in names:
        violations.append(_violation("W55-S3", relative, "checkpoint code lacks compact TaskDefinitionRef binding"))
    projection = _function(tree, "to_checkpoint_dict")
    if projection is not None:
        serialized_strings = {
            node.value
            for node in ast.walk(projection)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        for forbidden in ("contract", "spec", "phases"):
            if forbidden in serialized_strings:
                violations.append(_violation("W55-S3", relative, f"checkpoint projection serializes authority body field: {forbidden}", projection.lineno))
    return violations


def _check_s4(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    manager_relative = "agent/llm/context_manager.py"
    manager_tree = _tree(root, manager_relative)
    if manager_tree is None:
        violations.append(_violation("W55-S4", manager_relative, "ContextManager is missing or unparsable"))
    else:
        names = _names(manager_tree)
        for required in ("task_context_resolver", "build_trusted_task_context"):
            if required not in names:
                violations.append(_violation("W55-S4", manager_relative, f"missing trusted context seam: {required}"))
    call_relative = "agent/llm/context_model_call.py"
    call_tree = _tree(root, call_relative)
    if call_tree is None or "include_task_definition" not in _names(call_tree):
        violations.append(_violation("W55-S4", call_relative, "model call does not carry explicit task-definition inclusion"))
    resolver_relative = "agent/task_definition/resolver.py"
    resolver_tree = _tree(root, resolver_relative)
    if resolver_tree is None or not {"AUTHORITY_HEADER", "AUTHORITY_FOOTER"} <= _names(resolver_tree):
        violations.append(_violation("W55-S4", resolver_relative, "trusted resolver markers are missing"))
    return violations


def _check_s5(root: Path) -> list[ArchitectureViolation]:
    relative = "agent/llm/context_views.py"
    tree = _tree(root, relative)
    if tree is None:
        return [_violation("W55-S5", relative, "context compaction is missing or unparsable")]
    compact = _function(tree, "build_compact_view")
    if compact is None:
        return [_violation("W55-S5", relative, "build_compact_view is missing")]
    has_first_message_copy = any(
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "messages"
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == 0
        for node in ast.walk(compact)
    )
    if not has_first_message_copy:
        return [_violation("W55-S5", relative, "compaction does not retain the original system message")]
    return []


def _check_s6(root: Path) -> list[ArchitectureViolation]:
    relative = 'agent/orchestration/task_runner.py'
    tree = _tree(root, relative)
    if tree is None:
        return [_violation('W55-S6', relative, 'TaskRunner is missing or unparsable')]
    source = _source(root, relative) or ''
    helper_relative = 'agent/orchestration/task_definition_gate.py'
    helper_tree = _tree(root, helper_relative)
    helper_source = _source(root, helper_relative) or ''
    ensure = _function(tree, '_ensure_task_definition')
    gate = _function(helper_tree, 'ensure_task_definition') if helper_tree is not None else None
    run = _function(tree, 'run')
    execute = _function(tree, '_execute')
    shape_violation = _s6_gate_shape_violation(tree, helper_tree, source, helper_source, relative)
    if shape_violation is not None:
        return [shape_violation]
    assert ensure is not None
    assert gate is not None
    assert run is not None
    assert execute is not None
    violations: list[ArchitectureViolation] = []
    if 'resumed' not in _attribute_names(gate) or 'resume' not in _call_names(gate):
        violations.append(_violation('W55-S6', relative, 'resume path does not resolve the persisted authority', ensure.lineno))
    violations.extend(_s6_compiler_absence_violations(gate, helper_relative))
    violations.extend(_s6_result_violations(run, relative))
    violations.extend(_s6_order_violations(run, relative))
    return violations



def _check_s7(root: Path) -> list[ArchitectureViolation]:
    relative = 'agent/interfaces/cli/app.py'
    tree = _tree(root, relative)
    if tree is None:
        return [_violation('W55-S7', relative, 'CLI adapter is missing or unparsable')]
    command = _function(tree, '_run_task_context')
    helper_relative = 'agent/interfaces/cli/task_context.py'
    helper_tree = _tree(root, helper_relative)
    if helper_tree is not None:
        command = _function(helper_tree, 'run_task_context') or command
    if command is None:
        return [_violation('W55-S7', relative, 'model-free task context command is missing')]
    forbidden = {
        'AgentApplication', 'ChatSession', 'ModelGateway', 'create_application',
        'ToolExecutor', 'ToolRegistry', 'WorkspaceManager', 'CheckpointManager',
        'TaskDefinitionCompiler', 'BudgetExhausted', 'write_json_atomic',
        'ensure_directories', 'mkdir', 'write_text',
    }
    names = _names(command) | _call_names(command)
    imported = {
        alias.asname or alias.name.split('.')[-1]
        for node in ast.walk(command)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    found = sorted(forbidden & (names | imported))
    return [
        _violation('W55-S7', relative, 'task context command touches forbidden runtime: ' + name, command.lineno)
        for name in found
    ]



def _check_s8(root: Path) -> list[ArchitectureViolation]:
    scoped = [
        root / 'agent' / 'task_definition',
        root / 'agent' / 'orchestration' / 'task_runner.py',
        root / 'agent' / 'interfaces' / 'cli' / 'app.py',
    ]
    tokens = (
        'advance_' + 'phase',
        'autonomous_' + 'phase',
        'phase_' + 'decision',
        'llm_' + 'phase',
    )
    violations: list[ArchitectureViolation] = []
    for item in scoped:
        paths = item.rglob('*.py') if item.is_dir() else (item,)
        for path in paths:
            try:
                text = path.read_text(encoding='utf-8').lower()
            except (OSError, UnicodeDecodeError):
                continue
            for token in tokens:
                if token in text:
                    violations.append(_violation('W55-S8', path.relative_to(root).as_posix(), 'future phase policy construct: ' + token))
    return violations




def _check_s9(root: Path) -> list[ArchitectureViolation]:
    prior_scripts = ['scripts/check_production_naming_hygiene.py'] + [
        'scripts/check_' + 'wave' + str(index) + '_architecture.py'
        for index in range(1, 6)
    ]
    violations: list[ArchitectureViolation] = []
    for relative in prior_scripts:
        path = root / relative
        if not path.is_file():
            violations.append(_violation('W55-S9', relative, 'required prior architecture gate is missing'))
            continue
        try:
            result = subprocess.run(
                [sys.executable, str(path)],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            violations.append(_violation('W55-S9', relative, f'prior architecture gate could not run: {type(exc).__name__}'))
            continue
        if result.returncode != 0:
            output = (result.stdout or result.stderr or 'prior gate failed').strip().splitlines()
            detail = output[0] if output else 'prior gate failed'
            violations.append(_violation('W55-S9', relative, 'prior architecture gate failed: ' + detail))
    return violations


def check_architecture(root: str | Path = ".") -> list[ArchitectureViolation]:
    """Return scoped W55-S1..S9 violations for ``root``."""

    resolved = Path(root).expanduser().resolve()
    checks = (_check_s1, _check_s2, _check_s3, _check_s4, _check_s5, _check_s6, _check_s7, _check_s8, _check_s9)
    return [violation for check in checks for violation in check(resolved)]


find_violations = check_architecture
check_wave55_architecture = check_architecture


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check task-authority architecture boundaries")
    parser.add_argument("root", nargs="?", default=".", help="repository root")
    args = parser.parse_args(list(argv) if argv is not None else None)
    violations = check_architecture(args.root)
    if violations:
        for violation in violations:
            print(violation.format())
        return 1
    print("W55 architecture checks: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
