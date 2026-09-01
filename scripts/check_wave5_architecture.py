"""Bounded static ownership gates for Wave 5 mechanical primitives."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "agent"

FILEWRITER_RUNTIME = "agent/skills/file_writer_runtime.py"
FILEWRITER_SKILL = "agent/skills/file_writer.py"
CODE_WORKFLOW = "agent/code/workflow_application.py"
TRANSACTION_OWNER = "agent/code/change_transaction.py"

MODEL_WRITE_SURFACE = frozenset(
    {
        FILEWRITER_SKILL,
        FILEWRITER_RUNTIME,
        "agent/skills/code_task.py",
        TRANSACTION_OWNER,
        CODE_WORKFLOW,
        "agent/workspace.py",
    }
)

SCRATCH_FUNCTIONS = {
    FILEWRITER_RUNTIME: frozenset(
        {
            "prepare_workspace",
            "_write",
            "_append",
            "_patch",
            "_delete_lines",
        }
    ),
    FILEWRITER_SKILL: frozenset(
        {
            "_get_workspace_path",
            "_invalidate_cache",
            "_ast_patch",
        }
    ),
    "agent/workspace.py": frozenset({"create_restore_point", "_backup_file"}),
}

MEMORY_FILES = frozenset(
    {
        "agent/memory.py",
        "agent/semantic_memory.py",
        "agent/skills/session_memory.py",
    }
)

PROCESS_FILES = frozenset(
    {
        "agent/skills/shell.py",
        "agent/skills/shell_process.py",
        "agent/skills/python_executor.py",
        "agent/skills/python_process.py",
        "agent/code/validation_process.py",
        "agent/code/validation_external.py",
    }
)

WRITE_TERMINALS = frozenset(
    {
        "copy",
        "copy2",
        "copyfile",
        "copytree",
        "move",
        "mkdir",
        "open",
        "remove",
        "rename",
        "replace",
        "rmdir",
        "rmtree",
        "unlink",
        "write",
        "write_bytes",
        "write_text",
    }
)
WRITE_OPEN_MODES = frozenset({"a", "w", "x", "+"})
CHANGESET_NAMES = frozenset({"ChangeSetTransaction"})
GENERIC_EFFECT_NAMES = frozenset(
    {
        "effect",
        "effectexecutor",
        "effectframework",
        "genericeffectexecutor",
        "universaleffectexecutor",
    }
)


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _files() -> Iterable[Path]:
    if AGENT_ROOT.is_dir():
        yield from sorted(AGENT_ROOT.rglob("*.py"))


def _terminal_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _literal_string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _function_nodes(tree: ast.AST) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]:
    return tuple(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _enclosing_function(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
    return None


def _assigned_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        return set().union(*(_assigned_names(item) for item in target.elts))
    return set()


def _write_aliases(tree: ast.AST) -> set[str]:
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for imported in node.names:
                if imported.name in WRITE_TERMINALS and module in {"builtins", "os", "shutil"}:
                    aliases.add(imported.asname or imported.name)
        elif isinstance(node, ast.Assign):
            value = node.value
            value_name = _terminal_name(value)
            if value_name in WRITE_TERMINALS or (
                isinstance(value, ast.Name) and value.id in aliases
            ):
                for target in node.targets:
                    aliases.update(_assigned_names(target))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            value_name = _terminal_name(node.value)
            if value_name in WRITE_TERMINALS or (
                isinstance(node.value, ast.Name) and node.value.id in aliases
            ):
                aliases.update(_assigned_names(node.target))
    return aliases


def _open_mode(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        mode_node = node.args[0] if node.args else None
    elif isinstance(node.func, ast.Name) and node.func.id == "open":
        mode_node = node.args[1] if len(node.args) > 1 else None
    else:
        return None
    if mode_node is None:
        for keyword in node.keywords:
            if keyword.arg == "mode":
                mode_node = keyword.value
                break
    return _literal_string(mode_node)


def _is_write_call(node: ast.Call, aliases: set[str]) -> bool:
    terminal = _terminal_name(node.func)
    if terminal in {"open"}:
        mode = _open_mode(node)
        return mode is not None and any(flag in mode for flag in WRITE_OPEN_MODES)
    if isinstance(node.func, ast.Name):
        return terminal in aliases
    if (
        terminal == "replace"
        and isinstance(node.func, ast.Attribute)
        and (
            _terminal_name(node.func.value) == "dataclasses"
            or (
                isinstance(node.func.value, ast.Call)
                and isinstance(node.func.value.func, ast.Name)
                and node.func.value.func.id == "str"
            )
        )
    ):
        return False
    return terminal in WRITE_TERMINALS


def _filesystem_calls(tree: ast.AST) -> tuple[ast.Call, ...]:
    aliases = _write_aliases(tree)
    return tuple(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_write_call(node, aliases)
    )


def _changeset_aliases(tree: ast.AST) -> set[str]:
    aliases = set(CHANGESET_NAMES)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for imported in node.names:
                if imported.name == "ChangeSetTransaction":
                    aliases.add(imported.asname or imported.name)
        elif isinstance(node, ast.Assign):
            if isinstance(node.value, ast.Name) and node.value.id in aliases:
                for target in node.targets:
                    aliases.update(_assigned_names(target))
    return aliases


def _contains_changeset_call(
    node: ast.AST,
    changeset_aliases: set[str],
) -> bool:
    return any(
        isinstance(item, ast.Call)
        and (
            _terminal_name(item.func) == "ChangeSetTransaction"
            or (isinstance(item.func, ast.Name) and item.func.id in changeset_aliases)
        )
        for item in ast.walk(node)
    )


def _write_findings(
    tree: ast.AST,
    relative: str,
    *,
    gate: str,
    allowed_functions: frozenset[str] = frozenset(),
) -> list[str]:
    parents = _parent_map(tree)
    findings: list[str] = []
    for call in _filesystem_calls(tree):
        function = _enclosing_function(call, parents)
        if function is not None and function.name in allowed_functions:
            continue
        terminal = _terminal_name(call.func) or "filesystem"
        findings.append(
            f"{gate}: {relative}:{call.lineno}: direct filesystem mutation "
            f"'{terminal}' outside ChangeSetTransaction"
        )
    return findings


def _required_transaction_finding(
    tree: ast.AST,
    relative: str,
    *,
    function_name: str,
    gate: str,
    required: bool,
) -> list[str]:
    functions = tuple(
        function
        for function in _function_nodes(tree)
        if function.name == function_name
    )
    if not functions:
        if not required:
            return []
        return [
            f"{gate}: {relative}: required {function_name} transaction boundary is missing"
        ]
    aliases = _changeset_aliases(tree)
    if any(_contains_changeset_call(function, aliases) for function in functions):
        return []
    return [
        f"{gate}: {relative}:{functions[0].lineno}: "
        f"{function_name} does not use ChangeSetTransaction"
    ]


def _check_model_surface(
    tree: ast.AST,
    relative: str,
    *,
    required: bool,
) -> list[str]:
    if relative not in MODEL_WRITE_SURFACE:
        return []
    if relative == TRANSACTION_OWNER:
        return []

    allowed = SCRATCH_FUNCTIONS.get(relative, frozenset())
    findings = _write_findings(tree, relative, gate="W5-S4", allowed_functions=allowed)
    if relative in {FILEWRITER_RUNTIME, FILEWRITER_SKILL}:
        findings.extend(
            _write_findings(tree, relative, gate="W5-S1", allowed_functions=allowed)
        )
    if relative == CODE_WORKFLOW:
        findings.extend(
            _write_findings(tree, relative, gate="W5-S3", allowed_functions=allowed)
        )
    if relative == FILEWRITER_RUNTIME:
        findings.extend(
            _required_transaction_finding(
                tree,
                relative,
                function_name="review_and_commit",
                gate="W5-S1",
                required=required,
            )
        )
    if relative == CODE_WORKFLOW:
        findings.extend(
            _required_transaction_finding(
                tree,
                relative,
                function_name="apply_changes",
                gate="W5-S3",
                required=required,
            )
        )
    return findings


def _imports_changeset(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if any(imported.name == "ChangeSetTransaction" for imported in node.names):
                return True
        elif isinstance(node, ast.Import):
            if any(alias.name.endswith("change_transaction") for alias in node.names):
                return True
    return False


def _check_domain_separation(tree: ast.AST, relative: str) -> list[str]:
    if relative not in MEMORY_FILES and relative not in PROCESS_FILES:
        return []
    if not _imports_changeset(tree) and not any(
        _terminal_name(node.func) == "ChangeSetTransaction"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    ):
        return []
    return [
        f"W5-S5: {relative}: memory/process owner depends on ChangeSetTransaction"
    ]


def _check_generic_effect_framework(tree: ast.AST, relative: str) -> list[str]:
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name.casefold() in GENERIC_EFFECT_NAMES:
            findings.append(
                f"W5-S6: {relative}:{node.lineno}: generic Effect framework introduced"
            )
    return findings


def _check_tree(tree: ast.AST, relative: str, *, required: bool = False) -> list[str]:
    return sorted(
        {
            *_check_model_surface(tree, relative, required=required),
            *_check_domain_separation(tree, relative),
            *_check_generic_effect_framework(tree, relative),
        }
    )


def check_source(source: str, relative: str = "agent/adversarial.py") -> list[str]:
    """Check a source snippet with the same semantic W5 gates."""

    normalized = Path(relative).as_posix()
    return _check_tree(ast.parse(source, filename=normalized), normalized)


def _check_file(path: Path) -> list[str]:
    relative = _relative(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    except (OSError, SyntaxError) as exc:
        return [f"W5-PARSE: {relative}: {type(exc).__name__}: {exc}"]
    return _check_tree(tree, relative, required=True)


def run_checks() -> list[str]:
    findings: list[str] = []
    for path in _files():
        findings.extend(_check_file(path))
    return sorted(set(findings))


def main() -> int:
    findings = run_checks()
    if findings:
        print("Wave 5 architecture gates failed:")
        print("\n".join(f"- {finding}" for finding in findings))
        return 1
    print("Wave 5 architecture gates passed (S1-S6).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
