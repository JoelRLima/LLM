"""Adversarial static ownership gates for the Wave 4 observability spine."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

try:
    from scripts.observability_architecture_ast import (
        SymbolResolver,
        inside_snapshotless_boundary,
        is_raw_event_value,
        parent_map,
        raw_event_aliases,
    )
except ModuleNotFoundError:  # Direct script execution.
    from observability_architecture_ast import (  # type: ignore[no-redef]
        SymbolResolver,
        inside_snapshotless_boundary,
        is_raw_event_value,
        parent_map,
        raw_event_aliases,
    )

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "agent"
CORRELATION_OWNER = "agent/runtime/correlation.py"
SNAPSHOT_OWNER = "agent/reporting/run_snapshot.py"
EVENT_ADAPTER_FILES = frozenset(
    {"agent/state.py", "agent/runtime/event_dispatch.py", "agent/runtime/events.py"}
)
CORRELATION_ID_NAMES = frozenset(
    {"run_id", "root_task_id", "task_id", "parent_task_id", "node_id", "correlation"}
)
NON_CORRELATION_UUID_TARGETS = frozenset(
    {
        "change_set_id",
        "invocation_id",
        "snapshot_id",
        "step_id",
        "validation_invocation_id",
    }
)
NON_CORRELATION_UUID_FUNCTIONS = {
    ("agent/planning/plan_model.py", "_default_id_factory"),
    ("agent/runtime/instance_lock.py", "create"),
    ("agent/runtime/instance_lock.py", "_create"),
    ("agent/state_plan_execution.py", "_new_step_id"),
    ("agent/state_plan_execution.py", "set_plan"),
    ("agent/state_plan_execution.py", "insert_plan_step"),
    ("agent/tools/result_adapter.py", "from_legacy_result"),
    ("agent/tools/runtime_identity.py", "create"),
}
PROJECTION_PREFIXES = ("agent/reporting/", "agent/evaluation/", "agent/interfaces/")
UUID_TARGETS = frozenset(f"uuid.uuid{version}" for version in (1, 3, 4, 5))
CORRELATION_FACTORIES = frozenset({"fresh", "resume", "unrelated_task"})
STATUS_CALLS = frozenset(
    {
        "canonical_public_status",
        "normalize_terminal_status",
        "project_operational_outcome",
        "reconcile_report_status",
    }
)
METRIC_CALLS = frozenset(
    {
        "project_run_metrics",
        "aggregate_metrics",
        "_aggregate_metrics",
        "_aggregate_task_metrics",
        "metrics_snapshot_for_orchestrator",
    }
)
def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _files() -> Iterable[Path]:
    yield from sorted(AGENT_ROOT.rglob("*.py"))


def _literal_string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _contains_name(node: ast.AST, names: set[str] | frozenset[str]) -> bool:
    return any(isinstance(item, ast.Name) and item.id in names for item in ast.walk(node))


def _target_name(target: ast.AST) -> str | None:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _projection_file(relative: str) -> bool:
    return relative.startswith(PROJECTION_PREFIXES)


def _terminal_name(resolved: str | None) -> str:
    return resolved.rsplit(".", 1)[-1] if resolved else ""


def _uuid_is_correlation_use(
    node: ast.Call,
    resolver: SymbolResolver,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current: ast.AST = node
    while current in parents:
        parent = parents[current]
        if isinstance(parent, (ast.Assign, ast.AnnAssign)):
            targets = parent.targets if isinstance(parent, ast.Assign) else (parent.target,)
            if any(_target_name(target) in CORRELATION_ID_NAMES for target in targets):
                return True
        if isinstance(parent, ast.Call):
            if any(keyword.arg in CORRELATION_ID_NAMES for keyword in parent.keywords):
                return True
            if _terminal_name(resolver.resolve(parent.func)) == "RunCorrelation":
                return True
        current = parent
    return False


def _enclosing_functions(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> tuple[str, ...]:
    functions: list[str] = []
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(current.name)
    return tuple(functions)


def _inside_resolved_call(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    resolver: SymbolResolver,
    terminal: str,
) -> bool:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.Call) and _terminal_name(resolver.resolve(current.func)) == terminal:
            return True
    return False


def _uuid_has_non_correlation_target(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.Assign, ast.AnnAssign)):
            targets = current.targets if isinstance(current, ast.Assign) else (current.target,)
            if any(_target_name(target) in NON_CORRELATION_UUID_TARGETS for target in targets):
                return True
        if isinstance(current, ast.Call) and any(
            keyword.arg in NON_CORRELATION_UUID_TARGETS for keyword in current.keywords
        ):
            return True
    return False


def _uuid_call_is_narrowly_allowed(
    node: ast.Call,
    relative: str,
    resolver: SymbolResolver,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    if relative == CORRELATION_OWNER:
        return True
    functions = _enclosing_functions(node, parents)
    if relative == "agent/reporting/task_report.py" and any(
        name in functions for name in ("_generate_report_id", "generate_report_id")
    ):
        return True
    if relative == "agent/tool_executor.py" and _inside_resolved_call(
        node, parents, resolver, "_request"
    ):
        return True
    allowed_functions = {
        name for file_name, name in NON_CORRELATION_UUID_FUNCTIONS if file_name == relative
    }
    if any(function in allowed_functions for function in functions):
        return True
    if _uuid_is_correlation_use(node, resolver, parents):
        return False
    return _uuid_has_non_correlation_target(node, parents)


def _check_correlation_uuid(
    tree: ast.AST,
    relative: str,
    resolver: SymbolResolver,
) -> list[str]:
    parents = parent_map(tree)
    return [
        f"W4-S1: {relative}:{node.lineno}: runtime correlation UUID creation outside owner"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and resolver.resolve(node.func) in UUID_TARGETS
        and not _uuid_call_is_narrowly_allowed(node, relative, resolver, parents)
    ]


def _check_event_ownership(
    tree: ast.AST,
    relative: str,
    resolver: SymbolResolver,
) -> list[str]:
    if relative in EVENT_ADAPTER_FILES:
        return []
    findings: list[str] = []
    aliases = raw_event_aliases(tree, resolver)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        direct_events_append = (
            node.func.attr == "append"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "events"
        )
        raw_add_event = (
            node.func.attr == "add_event"
            and bool(node.args)
            and is_raw_event_value(node.args[0], resolver, aliases)
        )
        if direct_events_append or raw_add_event:
            findings.append(
                f"W4-S2: {relative}:{node.lineno}: raw/direct runtime event publication outside adapter"
            )
    for class_node in (
        node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and "sink" in node.name.casefold()
    ):
        for method in class_node.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) or method.name != "emit":
                continue
            positional = [
                arg
                for arg in (*method.args.posonlyargs, *method.args.args)
                if arg.arg != "self"
            ]
            if len(positional) >= 2:
                findings.append(
                    f"W4-S2: {relative}:{method.lineno}: event sink accepts free-form type and envelope"
                )
    return findings


def _check_reconstructed_correlation(
    tree: ast.AST,
    relative: str,
    resolver: SymbolResolver,
) -> list[str]:
    if not _projection_file(relative):
        return []
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            resolved = resolver.resolve(node.func) or ""
            if any(
                resolved.endswith(f".RunCorrelation.{factory}")
                for factory in CORRELATION_FACTORIES
            ):
                findings.append(
                    f"W4-S3: {relative}:{node.lineno}: report/eval creates runtime correlation"
                )
            if _terminal_name(resolved) == "next" and node.args:
                scanned_identity = any(
                    isinstance(item, ast.Subscript)
                    and _literal_string(item.slice) in {"run_id", "root_task_id", "task_id"}
                    for item in ast.walk(node.args[0])
                )
                if scanned_identity or _contains_name(
                    node.args[0], {"run_id", "root_task_id", "task_id"}
                ):
                    findings.append(
                        f"W4-S3: {relative}:{node.lineno}: report/eval reconstructs correlation from a scan"
                    )
        if isinstance(node, ast.Subscript):
            key = _literal_string(node.slice)
            if key in {"run_id", "root_task_id", "task_id"} and _contains_name(
                node.value, {"entries", "metrics", "history", "records", "filenames"}
            ):
                findings.append(
                    f"W4-S3: {relative}:{node.lineno}: report/eval reads runtime identity from a collection"
                )
    return findings


def _check_projection_ownership(
    tree: ast.AST,
    relative: str,
    resolver: SymbolResolver,
    *,
    gate: str,
    call_names: frozenset[str],
    message: str,
) -> list[str]:
    if not _projection_file(relative):
        return []
    parents = parent_map(tree)
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _terminal_name(resolver.resolve(node.func)) not in call_names:
            continue
        if inside_snapshotless_boundary(node, parents):
            continue
        findings.append(f"{gate}: {relative}:{node.lineno}: {message}")
    return findings


def _check_metric_budget_snapshots(tree: ast.AST, relative: str) -> list[str]:
    if not _projection_file(relative):
        return []
    parents = parent_map(tree)
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "snapshot" or not isinstance(node.func.value, ast.Name):
            continue
        if node.func.value.id not in {"ledger", "budget", "task_budget", "budget_snapshot"}:
            continue
        if not inside_snapshotless_boundary(node, parents):
            findings.append(
                f"W4-S5: {relative}:{node.lineno}: report/eval reconstructs metrics from a budget snapshot"
            )
    return findings


def _check_report_identity(
    tree: ast.AST,
    relative: str,
    resolver: SymbolResolver,
) -> list[str]:
    if not relative.startswith("agent/reporting/"):
        return []
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and "generate_task_id" in node.name:
            findings.append(
                f"W4-S6: {relative}:{node.lineno}: report builder generates task_id instead of report_id"
            )
        if isinstance(node, ast.Call) and "generate_task_id" in _terminal_name(
            resolver.resolve(node.func)
        ):
            findings.append(
                f"W4-S6: {relative}:{node.lineno}: report builder calls task identity generator"
            )
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                generated_uuid = (
                    isinstance(value, ast.Call)
                    and resolver.resolve(value.func) in UUID_TARGETS
                )
                if _literal_string(key) == "task_id" and generated_uuid:
                    findings.append(
                        f"W4-S6: {relative}:{node.lineno}: report artifact task_id is generated locally"
                    )
    return findings


def _check_tree(tree: ast.AST, relative: str) -> list[str]:
    resolver = SymbolResolver(tree)
    return sorted(
        {
            *_check_correlation_uuid(tree, relative, resolver),
            *_check_event_ownership(tree, relative, resolver),
            *_check_reconstructed_correlation(tree, relative, resolver),
            *_check_projection_ownership(
                tree,
                relative,
                resolver,
                gate="W4-S4",
                call_names=STATUS_CALLS,
                message="post-snapshot status/outcome recomputation",
            ),
            *_check_projection_ownership(
                tree,
                relative,
                resolver,
                gate="W4-S5",
                call_names=METRIC_CALLS,
                message="post-snapshot metrics reconstruction",
            ),
            *_check_metric_budget_snapshots(tree, relative),
            *_check_report_identity(tree, relative, resolver),
        }
    )


def check_source(source: str, relative: str = "agent/adversarial.py") -> list[str]:
    """Check a source snippet with the same gates used for repository files."""

    return _check_tree(ast.parse(source, filename=relative), relative)


def _check_file(path: Path) -> list[str]:
    relative = _relative(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    except (OSError, SyntaxError) as exc:
        return [f"W4-PARSE: {relative}: {type(exc).__name__}"]
    return _check_tree(tree, relative)


def run_checks() -> list[str]:
    findings: list[str] = []
    for path in _files():
        findings.extend(_check_file(path))
    return sorted(set(findings))


def main() -> int:
    findings = run_checks()
    if findings:
        print("Wave 4 architecture gates failed:")
        print("\n".join(f"- {finding}" for finding in findings))
        return 1
    print("Wave 4 architecture gates passed (S1-S6).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
