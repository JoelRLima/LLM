"""Deterministic source-only architecture gates for the W6 runtime seam."""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

CANONICAL_POLICY = "agent/runtime/task_policy.py"
POLICY_STATE_MODULE = "agent/runtime/task_policy_state.py"
POLICY_SUPPORT_MODULES = frozenset(
    {
        CANONICAL_POLICY,
        "agent/runtime/task_policy_engine.py",
        "agent/runtime/task_policy_state.py",
        "agent/runtime/task_policy_types.py",
    }
)
POLICY_STATE_FIELDS = frozenset({"_logical_work_units_consumed", "_active_elapsed_seconds"})
SOURCE_ROOT_NAMES = frozenset(
    {
        "state",
        "graph_state",
        "hierarchy_state",
        "step_records",
        "operational_outcome",
    }
)
MUTATING_METHODS = frozenset(
    {
        "append",
        "clear",
        "extend",
        "insert",
        "pop",
        "remove",
        "reverse",
        "sort",
        "set_plan",
        "mark_completed",
        "mark_failed",
        "mark_skipped",
        "mark_blocked",
        "mark_unverified",
        "mark_cancelled",
        "reset",
        "record",
        "consume",
        "execute",
        "run",
        "write",
        "save",
        "persist",
    }
)


@dataclass(frozen=True, slots=True)
class ArchitectureViolation:
    """One stable, source-local architecture finding."""

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


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _source(root: Path, relative: str) -> str | None:
    try:
        return (root / relative).read_text(encoding="utf-8")
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


def _functions(tree: ast.AST | None, name: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    if tree is None:
        return []
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]


def _function(tree: ast.AST | None, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    matches = _functions(tree, name)
    return matches[0] if matches else None


def _names(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    names = {
        item.id
        for item in ast.walk(node)
        if isinstance(item, ast.Name)
    }
    names.update(
        item.name
        for item in ast.walk(node)
        if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )
    names.update(
        item.attr
        for item in ast.walk(node)
        if isinstance(item, ast.Attribute)
    )
    names.update(
        item.asname or item.name.split(".")[-1]
        for item in ast.walk(node)
        if isinstance(item, ast.alias)
    )
    return names


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _call_names(node: ast.AST | None) -> set[str]:
    if node is None:
        return set()
    return {
        _call_name(item)
        for item in ast.walk(node)
        if isinstance(item, ast.Call) and _call_name(item)
    }


def _references_name(node: ast.AST | None, name: str) -> bool:
    if node is None:
        return False
    return any(
        (isinstance(item, ast.Name) and item.id == name)
        or (isinstance(item, ast.Attribute) and item.attr == name)
        or (isinstance(item, ast.Constant) and item.value == name)
        for item in ast.walk(node)
    )


def _calls(node: ast.AST | None, names: set[str] | frozenset[str]) -> list[ast.Call]:
    if node is None:
        return []
    return [
        item
        for item in ast.walk(node)
        if isinstance(item, ast.Call) and _call_name(item) in names
    ]


def _attribute_root(node: ast.Attribute) -> str | None:
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _assignment_attributes(node: ast.AST) -> list[ast.Attribute]:
    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets.extend(node.targets)
    elif isinstance(node, ast.AnnAssign):
        targets.append(node.target)
    elif isinstance(node, ast.AugAssign):
        targets.append(node.target)
    return [
        item
        for target in targets
        for item in ast.walk(target)
        if isinstance(item, ast.Attribute)
    ]


def _first_line(calls: Iterable[ast.Call]) -> int | None:
    lines = [call.lineno for call in calls]
    return min(lines) if lines else None


def _has_admitted_prefix(function: ast.AST | None, value_name: str) -> bool:
    if function is None:
        return False
    for node in ast.walk(function):
        if not isinstance(node, ast.Subscript) or not isinstance(node.value, ast.Name):
            continue
        if node.value.id != value_name or not isinstance(node.slice, ast.Slice):
            continue
        upper = node.slice.upper
        if isinstance(upper, ast.Attribute) and upper.attr == "admitted_units":
            return True
    return False


def _call_has_keyword(call: ast.Call, name: str) -> bool:
    return any(keyword.arg == name for keyword in call.keywords)


def _has_status_compare(node: ast.AST) -> bool:
    for item in ast.walk(node):
        if not isinstance(item, ast.Compare):
            continue
        operands = [item.left, *item.comparators]
        if any(isinstance(value, ast.Name) and value.id == "status" for value in operands) and any(
            isinstance(value, ast.Constant) and value.value == "succeeded" for value in operands
        ):
            return True
    return False


def _check_s1_shape(relative: str, tree: ast.AST) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    classes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "TaskRuntimePolicy"
    ]
    if len(classes) != 1:
        violations.append(_violation("W6-S1", relative, "canonical task policy must define exactly one TaskRuntimePolicy"))
    required = {"TaskPolicyDecision", "TaskPolicyResult", "TaskPolicyState", "TaskRuntimePolicy"}
    missing = sorted(required - _names(tree))
    if missing:
        violations.append(_violation("W6-S1", relative, "canonical policy symbols are missing: " + ", ".join(missing)))
    policy = classes[0] if len(classes) == 1 else None
    methods = {
        node.name
        for node in ast.walk(policy)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    } if policy is not None else set()
    for required_method in ("check_current", "admit_work_units", "authorize_recovery"):
        if required_method not in methods:
            violations.append(_violation("W6-S1", relative, f"canonical policy lacks {required_method}()"))
    return violations


def _check_s1_duplicates(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    policy_types = {
        "TaskRuntimePolicy",
        "TaskPolicyState",
        "TaskPolicyDecision",
        "TaskPolicyResult",
    }
    for path in sorted((root / "agent").rglob("*.py")):
        path_relative = _relative(path, root)
        if path_relative in POLICY_SUPPORT_MODULES:
            continue
        other = _tree(root, path_relative)
        if other is None:
            continue
        for node in ast.walk(other):
            if isinstance(node, ast.ClassDef) and node.name in policy_types:
                violations.append(
                    _violation("W6-S1", path_relative, f"duplicate W6 policy type {node.name}", node.lineno)
                )
    return violations


def _check_s1(root: Path) -> list[ArchitectureViolation]:
    relative = CANONICAL_POLICY
    tree = _tree(root, relative)
    if tree is None:
        return [_violation("W6-S1", relative, "canonical task policy module is missing or unparsable")]
    violations = _check_s1_shape(relative, tree)
    violations.extend(_check_s1_duplicates(root))
    return violations


def _check_s2(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    canonical = _tree(root, CANONICAL_POLICY)
    state_owner_relative = POLICY_STATE_MODULE if _source(root, POLICY_STATE_MODULE) is not None else CANONICAL_POLICY
    state_owner = _tree(root, state_owner_relative)
    if canonical is None or state_owner is None:
        return [_violation("W6-S2", CANONICAL_POLICY, "canonical task-policy state is missing or unparsable")]
    canonical_fields = {
        attribute.attr
        for node in ast.walk(state_owner)
        for attribute in _assignment_attributes(node)
        if attribute.attr in POLICY_STATE_FIELDS
    }
    missing = sorted(POLICY_STATE_FIELDS - canonical_fields)
    if missing:
        violations.append(_violation("W6-S2", state_owner_relative, "canonical state fields are missing: " + ", ".join(missing)))
    for path in sorted((root / "agent").rglob("*.py")):
        relative = _relative(path, root)
        if relative in POLICY_SUPPORT_MODULES:
            continue
        tree = _tree(root, relative)
        if tree is None:
            continue
        for node in ast.walk(tree):
            for attribute in _assignment_attributes(node):
                if attribute.attr in POLICY_STATE_FIELDS:
                    violations.append(
                        _violation(
                            "W6-S2",
                            relative,
                            f"task-scoped field {attribute.attr} has a second assignment owner",
                            attribute.lineno,
                        )
                    )
    return violations


def _check_s3(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    plan_relative = "agent/planning/plan_executor.py"
    plan_tree = _tree(root, plan_relative)
    plan_function = _function(plan_tree, "_execute_parallel_read_batch")
    plan_admit = _calls(plan_function, {"admit_work_units"})
    plan_dispatch = _calls(plan_function, {"_run_parallel_tools", "run_parallel_tools"})
    if plan_function is None or not plan_admit or not plan_dispatch:
        violations.append(_violation("W6-S3", plan_relative, "parallel plan dispatch lacks task-policy admission"))
    elif (_first_line(plan_admit) or 0) >= (_first_line(plan_dispatch) or 0):
        violations.append(_violation("W6-S3", plan_relative, "parallel plan dispatch occurs before admission", plan_function.lineno))
    if not _has_admitted_prefix(plan_function, "batch_indices"):
        violations.append(_violation("W6-S3", plan_relative, "parallel plan dispatch does not use the admitted prefix", getattr(plan_function, "lineno", None)))

    graph_relative = "agent/planning/task_scheduler.py"
    graph_tree = _tree(root, graph_relative)
    graph_function = _function(graph_tree, "_run_batch")
    graph_admit = _calls(graph_function, {"admit_work_units"})
    graph_dispatch = _calls(graph_function, {"submit"})
    if graph_function is None or not graph_admit or not graph_dispatch:
        violations.append(_violation("W6-S3", graph_relative, "graph batch dispatch lacks task-policy admission"))
    elif (_first_line(graph_admit) or 0) >= (_first_line(graph_dispatch) or 0):
        violations.append(_violation("W6-S3", graph_relative, "graph batch dispatch occurs before admission", graph_function.lineno))
    if not _has_admitted_prefix(graph_function, "batch"):
        violations.append(_violation("W6-S3", graph_relative, "graph dispatch does not use the admitted prefix", getattr(graph_function, "lineno", None)))
    return violations


def _check_s4(root: Path) -> list[ArchitectureViolation]:
    route_specs = (
        ("agent/planning/plan_executor.py", "_execute_index"),
        ("agent/planning/reactive_loop.py", "_limit_answer"),
        ("agent/planning/hierarchical_executor.py", "execute"),
        ("agent/orchestration/security_service.py", "run"),
        ("agent/planning/task_scheduler.py", "_run_batch"),
        ("agent/runtime/model_call.py", "_admit"),
    )
    violations: list[ArchitectureViolation] = []
    for relative, function_name in route_specs:
        tree = _tree(root, relative)
        function = _function(tree, function_name)
        if tree is None or function is None:
            violations.append(_violation("W6-S4", relative, f"required route function {function_name} is missing"))
            continue
        if not _references_name(function, "task_policy"):
            violations.append(_violation("W6-S4", relative, f"{function_name} does not reference the canonical task policy", function.lineno))
        if not _call_names(function) & {"admit_work_units", "check_current"}:
            violations.append(_violation("W6-S4", relative, f"{function_name} bypasses the W6 admission seam", function.lineno))
    graph_relative = "agent/planning/task_graph.py"
    graph_tree = _tree(root, graph_relative)
    graph_state = next(
        (
            node
            for node in ast.walk(graph_tree)
            if isinstance(node, ast.ClassDef) and node.name == "TaskGraphState"
        ),
        None,
    ) if graph_tree is not None else None
    graph_methods = {
        node.name
        for node in ast.walk(graph_state)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    } if graph_state is not None else set()
    if graph_state is None or not {"to_checkpoint_dict", "from_checkpoint_dict"} <= graph_methods:
        violations.append(_violation("W6-S4", graph_relative, "TaskGraphState lacks its explicit checkpoint boundary"))
    return violations


def _check_s5(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    context_relative = "agent/runtime/context.py"
    context_tree = _tree(root, context_relative)
    child = _function(context_tree, "child")
    if context_tree is None or child is None or "cancellation" not in _names(context_tree) or not _calls(child, {"replace"}):
        violations.append(_violation("W6-S5", context_relative, "child context does not preserve the canonical cancellation field"))

    session_relative = "agent/llm/session.py"
    session_tree = _tree(root, session_relative)
    if session_tree is None or "cancellation_token" not in _names(session_tree):
        violations.append(_violation("W6-S5", session_relative, "session root does not expose cancellation_token"))

    model_relative = "agent/runtime/model_call.py"
    model_tree = _tree(root, model_relative)
    for_session = _function(model_tree, "for_session")
    model_support_tree = _tree(root, "agent/runtime/model_call_support.py")
    if for_session is None or not (
        "cancellation_token" in _names(for_session)
        or "cancellation_token" in _names(model_support_tree)
    ):
        violations.append(_violation("W6-S5", model_relative, "model-call context does not read the session cancellation token"))

    security_relative = "agent/orchestration/security_service.py"
    security_tree = _tree(root, security_relative)
    security_run = _function(security_tree, "run")
    gateway_calls = [call for call in _calls(security_run, {"run"}) if _call_has_keyword(call, "cancellation_token")]
    if security_run is None or not gateway_calls:
        violations.append(_violation("W6-S5", security_relative, "security gateway call does not propagate cancellation_token"))
    return violations


def _has_frozen_dataclass(node: ast.ClassDef) -> bool:
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Name) or decorator.func.id != "dataclass":
            continue
        frozen = next((keyword.value for keyword in decorator.keywords if keyword.arg == "frozen"), None)
        if isinstance(frozen, ast.Constant) and frozen.value is True:
            return True
    return False


def _check_s6_shape(relative: str, tree: ast.AST) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    projection_classes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "TaskProgressProjection"
    ]
    if not projection_classes or not _has_frozen_dataclass(projection_classes[0]):
        violations.append(_violation("W6-S6", relative, "progress projection must be a frozen dataclass"))
    if _function(tree, "build_task_progress_projection") is None:
        violations.append(_violation("W6-S6", relative, "canonical progress builder is missing"))
    return violations


def _check_s6_node(relative: str, node: ast.AST) -> list[ArchitectureViolation]:
    forbidden_imports = (
        "agent.state",
        "agent.orchestration",
        "agent.reporting.task_tracker",
        "agent.planning.task_completion",
        "agent.planning.task_scheduler",
        "agent.planning.hierarchical_executor",
        "agent.runtime.task_policy",
    )
    violations: list[ArchitectureViolation] = []
    if isinstance(node, ast.ImportFrom) and any(
        (node.module or "") == forbidden or (node.module or "").startswith(forbidden + ".")
        for forbidden in forbidden_imports
    ):
        violations.append(_violation("W6-S6", relative, "projection imports an execution owner", node.lineno))
    if isinstance(node, ast.Import) and any(
        alias.name == forbidden or alias.name.startswith(forbidden + ".")
        for alias in node.names
        for forbidden in forbidden_imports
    ):
        violations.append(_violation("W6-S6", relative, "projection imports an execution owner", node.lineno))
    for attribute in _assignment_attributes(node):
        if _attribute_root(attribute) in SOURCE_ROOT_NAMES:
            violations.append(_violation("W6-S6", relative, "projection mutates source execution state", attribute.lineno))
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in {"setattr", "delattr"}:
            violations.append(_violation("W6-S6", relative, "projection uses dynamic mutation", node.lineno))
        if isinstance(node.func, ast.Attribute) and node.func.attr in MUTATING_METHODS:
            if _attribute_root(node.func) in SOURCE_ROOT_NAMES:
                violations.append(_violation("W6-S6", relative, "projection calls a source-state mutator", node.lineno))
    return violations


def _check_s6(root: Path) -> list[ArchitectureViolation]:
    relative = "agent/planning/task_progress_projection.py"
    tree = _tree(root, relative)
    if tree is None:
        return [_violation("W6-S6", relative, "progress projection is missing or unparsable")]
    violations = _check_s6_shape(relative, tree)
    for node in ast.walk(tree):
        violations.extend(_check_s6_node(relative, node))
    return violations


def _check_s7_tracker(root: Path) -> list[ArchitectureViolation]:
    tracker_relative = "agent/reporting/task_tracker.py"
    tracker_tree = _tree(root, tracker_relative)
    recompute = _function(tracker_tree, "_recompute_progress")
    if recompute is None or "build_task_progress_projection" not in _call_names(recompute):
        return [_violation("W6-S7", tracker_relative, "tracker progress does not consume the canonical projection")]
    return []


def _check_s7_rendering(root: Path) -> list[ArchitectureViolation]:
    rendering_relative = "agent/reporting/task_tracker_rendering.py"
    rendering = (_source(root, rendering_relative) or "").casefold()
    if "terminal_coverage_percent" not in rendering and "cobertura terminal" not in rendering and "terminal coverage" not in rendering:
        return [_violation("W6-S7", rendering_relative, "tracker rendering omits terminal coverage")]
    return []


def _check_s7_facts(root: Path) -> list[ArchitectureViolation]:
    facts_relative = "agent/reporting/run_projection_facts.py"
    facts_tree = _tree(root, facts_relative)
    if facts_tree is None or "build_task_progress_projection" not in _call_names(facts_tree):
        return [_violation("W6-S7", facts_relative, "run projection facts do not consume canonical progress")]
    return []


def _check_s7_report(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    report_relative = "agent/reporting/task_report.py"
    report_tree = _tree(root, report_relative)
    build_report = _function(report_tree, "build_report")
    if build_report is None:
        return [_violation("W6-S7", report_relative, "task report builder is missing")]
    success_values: list[ast.AST] = []
    for node in ast.walk(build_report):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=False):
            if isinstance(key, ast.Constant) and key.value == "success" and value is not None:
                success_values.append(value)
    if not success_values or not _has_status_compare(build_report):
        violations.append(_violation("W6-S7", report_relative, "report success is not explicitly derived from operational status", build_report.lineno))
    for success_value in success_values:
        if "progress" in _names(success_value) or "percent" in _names(success_value):
            violations.append(_violation("W6-S7", report_relative, "report success is derived from progress percentage", getattr(success_value, "lineno", None)))
    return violations


def _check_s7(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for check in (_check_s7_tracker, _check_s7_rendering, _check_s7_facts, _check_s7_report):
        violations.extend(check(root))
    return violations


def _check_s8(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    required = (
        ("agent/task_definition/models.py", "TaskDefinitionRef"),
        ("agent/orchestration/task_definition_gate.py", "ensure_task_definition"),
        ("agent/orchestration/task_runner.py", "_ensure_task_definition"),
    )
    for relative, symbol in required:
        tree = _tree(root, relative)
        if tree is None or symbol not in _names(tree):
            violations.append(_violation("W6-S8", relative, f"W5.5 task-definition authority symbol is missing: {symbol}"))
    task_root = root / "agent" / "task_definition"
    if task_root.is_dir():
        forbidden = ("taskruntimepolicy", "taskpolicystate", "task_policy", "task_progress_projection")
        for path in sorted(task_root.rglob("*.py")):
            try:
                source = path.read_text(encoding="utf-8").casefold()
            except (OSError, UnicodeDecodeError):
                continue
            for token in forbidden:
                if token in source:
                    violations.append(_violation("W6-S8", _relative(path, root), "task-definition authority imports W6 policy/progress state"))
                    break
    if not (root / "scripts" / "check_wave55_architecture.py").is_file():
        violations.append(_violation("W6-S8", "scripts/check_wave55_architecture.py", "W5.5 authority checker is missing"))
    return violations


def _prior_checker_paths() -> tuple[str, ...]:
    return (
        "scripts/check_production_naming_hygiene.py",
        *(f"scripts/check_wave{index}_architecture.py" for index in range(1, 6)),
        "scripts/check_wave55_architecture.py",
    )


def _check_s9(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for relative in _prior_checker_paths():
        path = root / relative
        if not path.is_file():
            violations.append(_violation("W6-S9", relative, "required prior architecture gate is missing"))
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
            violations.append(_violation("W6-S9", relative, f"prior architecture gate could not run: {type(exc).__name__}"))
            continue
        if result.returncode != 0:
            output = (result.stdout or result.stderr or "prior gate failed").strip().splitlines()
            detail = output[0] if output else "prior gate failed"
            violations.append(_violation("W6-S9", relative, "prior architecture gate failed: " + detail))
    return violations


def check_architecture(root: str | Path = ".") -> list[ArchitectureViolation]:
    """Return deterministic W6-S1..S9 violations without importing agent runtime code."""

    resolved = Path(root).expanduser().resolve()
    checks = (
        _check_s1,
        _check_s2,
        _check_s3,
        _check_s4,
        _check_s5,
        _check_s6,
        _check_s7,
        _check_s8,
        _check_s9,
    )
    return [violation for check in checks for violation in check(resolved)]


find_violations = check_architecture
check_wave6_architecture = check_architecture


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check W6 task-policy and progress architecture")
    parser.add_argument("root", nargs="?", default=".", help="repository root")
    args = parser.parse_args(list(argv) if argv is not None else None)
    violations = check_architecture(args.root)
    if violations:
        for violation in violations:
            print(violation.format())
        return 1
    print("W6 architecture checks: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
