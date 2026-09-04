"""Small source-only ownership checks for W11 task directives."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]

DIRECTIVE_ROOTS = (
    "agent/runtime/task_directives.py",
    "agent/interfaces/task_directives.py",
    "agent/orchestration/task_directive_runtime.py",
)
CLI_ADAPTERS = (
    "agent/interfaces/task_directives.py",
    "agent/interfaces/cli/app.py",
    "agent/interfaces/cli/command_handlers.py",
    "agent/interfaces/cli/task_continuity.py",
)
CHECKPOINT_FILES = (
    "agent/runtime/task_directives.py",
    "agent/state_checkpoint.py",
    "agent/state_checkpoint_restore.py",
)
W11_CHECKPOINT_FIELDS = frozenset(
    {"schema_version", "directive", "deliberation_profile", "subject"}
)
FORBIDDEN_EFFECTIVE_FIELDS = frozenset(
    {
        "allowed_capabilities",
        "approval",
        "approval_granted",
        "approvals",
        "assume_yes",
        "capability_ceiling",
        "effective_reasoning",
        "effective_reasoning_budget",
        "model",
        "model_profile",
        "operational_mode",
        "provider",
        "reasoning_budget",
        "thinking_budget",
    }
)
FORBIDDEN_PLAN_NAMES = frozenset(
    {
        "execute_validated_plan",
        "PlanExecutor",
        "ToolExecutor",
        "ToolInvocationAdapter",
        "ToolInvocationGateway",
        "InvocationGateway",
    }
)
FORBIDDEN_RESUME_IMPORTS = frozenset(
    {
        "agent.checkpoint_manager",
        "agent.observability.trace_store",
        "agent.state_checkpoint",
    }
)
FORBIDDEN_RESUME_CALLS = frozenset(
    {
        "CheckpointManager",
        "TraceStore",
        "delete_checkpoint",
        "load_checkpoint",
        "save_checkpoint",
        "write_checkpoint",
        "_delete_checkpoint",
        "_load_checkpoint",
        "_save_checkpoint",
    }
)
FORBIDDEN_PROFILE_IMPORT_PREFIXES = (
    "agent.interfaces.cli.bootstrap",
    "agent.llm.provider",
    "agent.llm.providers",
    "agent.runtime.config",
)
FORBIDDEN_PROFILE_IMPORT_NAMES = frozenset(
    {
        "ConfigRepository",
        "ModelGateway",
        "ProviderFactory",
        "resolve_model_profile",
        "select_model",
        "set_model_profile",
    }
)


@dataclass(frozen=True, slots=True)
class ArchitectureViolation:
    """One deterministic W11 source-local architecture finding."""

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
    return path.resolve().relative_to(root.resolve()).as_posix()


def _tree(root: Path, relative: str) -> ast.Module | None:
    try:
        return ast.parse((root / relative).read_text(encoding="utf-8"), filename=relative)
    except (OSError, SyntaxError, UnicodeError):
        return None


def _violation(rule: str, relative: str, detail: str, node: ast.AST | None = None) -> ArchitectureViolation:
    return ArchitectureViolation(rule, relative, detail, getattr(node, "lineno", None))


def _module_name(node: ast.Import | ast.ImportFrom) -> str:
    if isinstance(node, ast.Import):
        return node.names[0].name if node.names else ""
    return node.module or ""


def _node_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _functions(tree: ast.Module | None, name: str) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    if tree is None:
        return
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            yield node


def _string_dict_keys(tree: ast.Module | None) -> Iterator[tuple[str, ast.AST]]:
    if tree is None:
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                yield key.value, node


def _check_s1_continue_authority(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in CLI_ADAPTERS:
        tree = _tree(root, relative)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = _module_name(node)
                direct_trace_authority = module == "agent.observability.trace_store" and any(
                    alias.name == "TraceStore" for alias in node.names
                )
                if module in FORBIDDEN_RESUME_IMPORTS - {"agent.observability.trace_store"} or direct_trace_authority:
                    findings.append(_violation("W11-S1", relative, "W11 adapter imports checkpoint/trace authority directly", node))
                for alias in node.names:
                    if alias.name in FORBIDDEN_RESUME_CALLS:
                        findings.append(_violation("W11-S1", relative, "W11 adapter imports a checkpoint authority", node))
            elif isinstance(node, ast.Call) and _node_name(node) in FORBIDDEN_RESUME_CALLS:
                findings.append(_violation("W11-S1", relative, "W11 adapter manipulates checkpoint authority directly", node))
    app_source = _tree(root, "agent/interfaces/cli/app.py")
    handler_source = _tree(root, "agent/interfaces/cli/command_handlers.py")
    route_names = {
        node.id
        for tree in (app_source, handler_source)
        if tree is not None
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    }
    route_attributes = {
        node.attr
        for tree in (app_source, handler_source)
        if tree is not None
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }
    if "run_task_resume" not in route_names and "resume" not in route_attributes:
        findings.append(
            _violation(
                "W11-S1",
                "agent/interfaces/cli/app.py",
                "W11 CONTINUE does not delegate to the W10 resume/application boundary",
            )
        )
    return findings


def _check_s2_plan_validation_owner(root: Path) -> list[ArchitectureViolation]:
    relative = "agent/planning/plan_preview.py"
    tree = _tree(root, relative)
    if tree is None:
        return []
    findings: list[ArchitectureViolation] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = _module_name(node)
            if module.startswith("agent.tools.invocation") or module.startswith("agent.orchestration.plan_executor"):
                findings.append(_violation("W11-S2", relative, "PLAN preview imports an execution owner", node))
            if any(alias.name in FORBIDDEN_PLAN_NAMES for alias in node.names):
                findings.append(_violation("W11-S2", relative, "PLAN preview imports a forbidden execution owner", node))
        elif isinstance(node, (ast.Name, ast.Attribute)) and _node_name(node) in FORBIDDEN_PLAN_NAMES:
            findings.append(_violation("W11-S2", relative, "PLAN preview references an execution owner", node))
    validation_seen = any(
        isinstance(node, ast.Constant)
        and node.value == "validate_and_optimize_plan"
        for node in ast.walk(tree)
    )
    if not validation_seen:
        findings.append(_violation("W11-S2", relative, "PLAN preview does not reference the validation-only seam"))
    return findings


def _check_s3_profile_separation(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in DIRECTIVE_ROOTS:
        tree = _tree(root, relative)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            module = _module_name(node)
            if module.startswith(FORBIDDEN_PROFILE_IMPORT_PREFIXES):
                findings.append(_violation("W11-S3", relative, "task profile owner imports model/provider configuration", node))
            if any(alias.name in FORBIDDEN_PROFILE_IMPORT_NAMES for alias in node.names):
                findings.append(_violation("W11-S3", relative, "task profile owner imports a model/provider selector", node))
    return findings


def _check_s4_operational_mode_separation(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    for relative in DIRECTIVE_ROOTS:
        tree = _tree(root, relative)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)) and _node_name(node) == "set_operational_mode":
                findings.append(_violation("W11-S4", relative, "task directive code mutates OperationalMode", node))
    return findings


def _check_s5_interactive_read(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    handlers = _tree(root, "agent/interfaces/cli/command_handlers.py")
    if not any(_functions(handlers, "read_file")):
        findings.append(_violation("W11-S5", "agent/interfaces/cli/command_handlers.py", "existing /read file-reader handler is missing"))
    commands = _tree(root, "agent/interfaces/cli/commands.py")
    if commands is None or not any(
        isinstance(node, ast.Constant) and node.value == "/read"
        for node in ast.walk(commands)
    ):
        findings.append(_violation("W11-S5", "agent/interfaces/cli/commands.py", "registered /read command path is missing"))
    return findings


def _check_s6_checkpoint_projection(root: Path) -> list[ArchitectureViolation]:
    findings: list[ArchitectureViolation] = []
    relative = "agent/runtime/task_directives.py"
    tree = _tree(root, relative)
    serializers = list(_functions(tree, "to_checkpoint_dict"))
    for function in serializers:
        dicts = [node for node in ast.walk(function) if isinstance(node, ast.Dict)]
        keys = {
            key.value
            for item in dicts
            for key in item.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        if keys != W11_CHECKPOINT_FIELDS:
            findings.append(_violation("W11-S6", relative, "W11 checkpoint projection must contain only typed directive fields", function))
    for relative in CHECKPOINT_FILES[1:]:
        tree = _tree(root, relative)
        for key, node in _string_dict_keys(tree):
            if key.casefold() in FORBIDDEN_EFFECTIVE_FIELDS:
                findings.append(_violation("W11-S6", relative, f"checkpoint projection persists effective field {key!r}", node))
    return findings


def _check_s7_w10(root: Path) -> list[ArchitectureViolation]:
    try:
        from scripts import check_wave10_architecture
    except ImportError as exc:
        try:
            import check_wave10_architecture as fallback_checker
        except ImportError:
            return [_violation("W11-S7", "scripts/check_wave10_architecture.py", f"W10 checker unavailable: {type(exc).__name__}")]
        return [
            ArchitectureViolation(item.rule_id, item.path, item.detail, item.line)
            for item in fallback_checker.check_architecture(root)
        ]
    return [
        ArchitectureViolation(item.rule_id, item.path, item.detail, item.line)
        for item in check_wave10_architecture.check_architecture(root)
    ]


_CHECKS = (
    _check_s1_continue_authority,
    _check_s2_plan_validation_owner,
    _check_s3_profile_separation,
    _check_s4_operational_mode_separation,
    _check_s5_interactive_read,
    _check_s6_checkpoint_projection,
    _check_s7_w10,
)


def check_architecture(root: str | Path = ".") -> list[ArchitectureViolation]:
    resolved = Path(root).expanduser().resolve()
    return [finding for check in _CHECKS for finding in check(resolved)]


def check_source(path: str | Path, root: str | Path | None = None) -> list[ArchitectureViolation]:
    resolved_root = Path(root).expanduser().resolve() if root is not None else ROOT
    source = Path(path).expanduser().resolve()
    try:
        relative = _relative(source, resolved_root)
    except ValueError:
        return [_violation("W11-S0", str(source), "source is outside repository root")]
    return [finding for finding in check_architecture(resolved_root) if finding.path == relative]


find_violations = check_architecture
check_wave11_architecture = check_architecture


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check W11 task-directive ownership boundaries")
    parser.add_argument("root", nargs="?", default=".", help="repository root")
    args = parser.parse_args(list(argv) if argv is not None else None)
    violations = check_architecture(args.root)
    if violations:
        for violation in violations:
            print(violation.format())
        return 1
    print("W11 architecture checks: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
