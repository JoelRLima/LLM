"""Deterministic source-only architecture gates for W8."""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# These are the only source locations allowed to own the corresponding
# low-level mechanisms.  The lists are deliberately exact: a new production
# owner must be reviewed rather than silently inheriting a directory-wide
# exception.
PROVIDER_OWNERS = frozenset(
    {
        "agent/code/workflow_proposal.py",
        "agent/evaluation/trace.py",
        "agent/llm/providers/openai_compatible.py",
        "agent/llm/session_requests.py",
        "agent/runtime/model_call.py",
        "agent/runtime/model_call_stream.py",
    }
)
ATOMIC_OWNERS = frozenset(
    {
        "agent/code/change_transaction.py",
        "agent/health/runtime_checks.py",
        "agent/memory/json_persistence.py",
        "agent/runtime/filesystem_primitives.py",
        "agent/runtime/instance_lock.py",
        "agent/runtime/state_migration.py",
        "agent/task_definition/repository_support.py",
        "agent/tools/extension_catalog_storage.py",
        "agent/tools/stdio_launcher.py",
    }
)
PROCESS_OWNERS = frozenset(
    {
        "agent/code/validation_process.py",
        "agent/evaluation/evaluation_identity.py",
        "agent/llm/context_views.py",
        "agent/skills/python_executor.py",
        "agent/skills/python_process.py",
        "agent/skills/shell_process.py",
        "agent/tools/process_tree.py",
        "agent/tools/stdio_launcher.py",
        "agent/tools/stdio_process.py",
    }
)
PATH_OWNERS = frozenset(
    {
        "agent/runtime/path_safety.py",
    }
)
PROFILE_OWNERS = frozenset(
    {
        "agent/code/workflow_proposal.py",
        "agent/evaluation/analysis_identity_support.py",
        "agent/evaluation/campaign_identity_records.py",
        "agent/interfaces/cli/command_handlers.py",
        "agent/llm/identity.py",
        "agent/llm/model_metrics.py",
        "agent/llm/model_profile.py",
        "agent/llm/model_profile_compat.py",
        "agent/llm/providers/openai_compatible.py",
        "agent/reporting/task_tracker.py",
        "agent/runtime/config_effective.py",
        "agent/runtime/config_schema.py",
        "agent/runtime/config_validation.py",
    }
)
EVENT_BUILDERS = frozenset(
    {
        "agent/orchestration/operations.py",
        "agent/runtime/context.py",
        "agent/runtime/task_policy_engine.py",
        "agent/tools/invocation_commit.py",
    }
)
REPORTING_OWNERS = frozenset(
    {
        "agent/application_result.py",
        "agent/reporting/metrics.py",
        "agent/reporting/operational_outcome.py",
        "agent/reporting/public_projection.py",
        "agent/reporting/run_projection_facts.py",
        "agent/reporting/run_receipt.py",
        "agent/reporting/run_receipt_builder.py",
        "agent/reporting/run_receipt_support.py",
        "agent/reporting/run_snapshot.py",
        "agent/reporting/task_report.py",
        "agent/reporting/task_report_rendering.py",
    }
)
RAW_RESULT_BOUNDARIES = frozenset(
    {
        "agent/health/runtime_checks.py",
        "agent/parsers.py",
        "agent/planning/deferred_execution.py",
        "agent/planning/completion_observations.py",
        "agent/planning/observation_invalidation.py",
        "agent/planning/provenance_validation.py",
        "agent/planning/parallel_contracts.py",
        "agent/planning/result_binding_values.py",
        "agent/planning/result_bindings_resolution.py",
        "agent/planning/task_semantics_inference.py",
        "agent/reporting/invocation_evidence.py",
        "agent/reporting/observation_evidence.py",
        "agent/reporting/run_projection_facts.py",
        "agent/reporting/run_receipt.py",
        "agent/reporting/task_report.py",
        "agent/reporting/task_report_rendering.py",
        "agent/evaluation/oracle_observations.py",
        "agent/evaluation/oracle_rules.py",
        "agent/interfaces/cli/command_handlers.py",
        "agent/orchestration/task_lifecycle.py",
        "agent/security/security_scanner.py",
        "agent/state_failure_recovery.py",
        "agent/runtime/operational_outcome_evidence.py",
        "agent/skills/file_reader_evidence.py",
        "agent/skills/file_writer_runtime.py",
        "agent/planning/reasoning_boundary.py",
        "agent/tools/builtin_adapter.py",
        "agent/tools/result_adapter.py",
        "agent/tools/result_completeness.py",
    }
)
PATH_AUTHORITY_BOUNDARIES = frozenset(
    {
        "agent/orchestrator.py",
        "agent/orchestration/hierarchical_service.py",
        "agent/orchestration/subsystems.py",
        "agent/interfaces/cli/workspace_entry.py",
    }
)
EXPLICIT_CWD_SELECTION_FUNCTIONS = frozenset({"argument_workspace", "choose_workspace"})
RETIRED_NAMES = frozenset(
    {
        "LegacyEventSinkAdapter",
        "event_emitter",
        "_call_extension",
        "legacy_reviewer",
        "LegacyToolInvoker",
        "ModelClient",
        "PendingStream",
        "RetryPolicy",
        "ResourceClaim",
        "normalize_resource_name",
        "failure_fact_from_legacy_message",
    }
)
RETIRED_MODULES = frozenset(
    {
        "agent/llm/model_client.py",
        "agent/planning/replan_compat.py",
        "agent/runtime/model_call_legacy.py",
        "agent/tools/legacy_invoker.py",
    }
)
PROFILE_KEYS = frozenset(
    {
        "api_url",
        "base_url",
        "default_model_profile",
        "model",
        "model_profiles",
        "provider",
        "provider_options",
    }
)
STDIO_BOUNDARY_FILES = frozenset(
    {
        "agent/tools/extension_manifest_parser.py",
        "agent/tools/stdio_adapter.py",
    }
)
TEXT_FIELDS = frozenset({"error", "message", "reason", "summary"})
TEXT_POLICY_FUNCTION_PARTS = frozenset(
    {"classif", "decide", "policy", "recover", "repair", "replan", "retry"}
)
RENDERING_FUNCTION_PARTS = frozenset({"display", "format", "log", "render"})
TEXT_POLICY_METHODS = frozenset({"endswith", "startswith"})
REGEX_POLICY_METHODS = frozenset({"fullmatch", "match", "search"})
POLICY_WORDS = frozenset(
    {
        "blocked",
        "cancel",
        "fail",
        "retry",
        "success",
        "timeout",
    }
)
PRIOR_CHECKERS = (
    "scripts/check_production_naming_hygiene.py",
    *(f"scripts/check_wave{index}_architecture.py" for index in range(1, 8)),
    "scripts/check_wave55_architecture.py",
)


@dataclass(frozen=True, slots=True)
class ArchitectureViolation:
    """One stable, source-local W8 finding."""

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


def _violation(
    rule_id: str,
    path: str,
    detail: str,
    line: int | None = None,
) -> ArchitectureViolation:
    return ArchitectureViolation(rule_id, path, detail, line)


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


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


def _agent_files(root: Path) -> Iterator[Path]:
    agent_root = root / "agent"
    if agent_root.is_dir():
        yield from sorted(agent_root.rglob("*.py"))


def _trees(root: Path) -> Iterator[tuple[str, ast.AST]]:
    for path in _agent_files(root):
        relative = _relative(path, root)
        tree = _tree(root, relative)
        if tree is not None:
            yield relative, tree


def _definitions(tree: ast.AST) -> Iterator[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _call_attribute(call: ast.Call) -> str:
    return call.func.attr if isinstance(call.func, ast.Attribute) else ""


def _literal_string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _mapping_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "get" and node.args:
            return _literal_string(node.args[0])
    if isinstance(node, ast.Subscript):
        return _literal_string(node.slice)
    return None


def _contains_mapping_key(node: ast.AST, keys: frozenset[str]) -> bool:
    return any(
        _mapping_key(child) in keys
        for child in ast.walk(node)
    )


def _is_human_text_attribute(node: ast.Attribute) -> bool:
    return node.attr in TEXT_FIELDS


def _is_human_text_expression(node: ast.AST, aliases: set[str]) -> bool:
    """Recognize bounded aliases derived from human-facing failure text."""

    if isinstance(node, ast.Name):
        return node.id in aliases
    if isinstance(node, ast.Attribute):
        return _is_human_text_attribute(node) or _is_human_text_expression(node.value, aliases)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
        return False
    if isinstance(node, ast.Call) and _mapping_key(node) in TEXT_FIELDS:
        return True
    if isinstance(node, ast.Subscript) and _mapping_key(node) in TEXT_FIELDS:
        return True
    return any(_is_human_text_expression(child, aliases) for child in ast.iter_child_nodes(node))


def _text_aliases(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    aliases: set[str] = set()
    assignments = [
        node
        for node in ast.walk(function)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    for _ in range(len(assignments) + 1):
        changed = False
        for assignment in assignments:
            value = assignment.value
            if value is None:
                continue
            if not _is_human_text_expression(value, aliases):
                continue
            targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases.add(target.id)
                    changed = True
        if not changed:
            break
    return aliases


def _is_policy_boundary(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    name = function.name.casefold()
    policy_parts = TEXT_POLICY_FUNCTION_PARTS - {"classif"}
    if any(part in name for part in RENDERING_FUNCTION_PARTS) and not any(part in name for part in policy_parts):
        return False
    return any(part in name for part in TEXT_POLICY_FUNCTION_PARTS)


def _has_text_policy_comparison(
    node: ast.Compare,
    aliases: set[str],
) -> bool:
    if not any(_is_human_text_expression(side, aliases) for side in [node.left, *node.comparators]):
        return False
    return any(
        isinstance(operator, (ast.In, ast.NotIn, ast.Eq, ast.NotEq))
        for operator in node.ops
    ) and _contains_policy_literal(node)


def _has_text_policy_call(node: ast.Call, aliases: set[str]) -> bool:
    if not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr in TEXT_POLICY_METHODS:
        return _is_human_text_expression(node.func.value, aliases) and _contains_policy_literal(node)
    if node.func.attr in REGEX_POLICY_METHODS:
        return (
            bool(node.args)
            and _contains_policy_literal(node.args[0])
            and any(_is_human_text_expression(arg, aliases) for arg in node.args[1:])
        )
    return False


def _contains_policy_literal(node: ast.AST) -> bool:
    for child in ast.walk(node):
        value = _literal_string(child)
        if value is not None and any(word in value.casefold() for word in POLICY_WORDS):
            return True
    return False


def _qualified_call(call: ast.Call, module: str, names: frozenset[str]) -> bool:
    return (
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == module
        and call.func.attr in names
    )


def _imported_names(tree: ast.AST, module: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _model_call_service_names(tree: ast.AST) -> set[str]:
    """Track local variables bound to the canonical model-call service."""

    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        value = node.value
        if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Attribute):
            continue
        receiver = value.func.value
        if not isinstance(receiver, ast.Name) or receiver.id != "ModelCallService":
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names.update(target.id for target in targets if isinstance(target, ast.Name))
    return names


def _is_model_call_service_invocation(call: ast.Call, service_names: set[str]) -> bool:
    if not isinstance(call.func, ast.Attribute):
        return False
    receiver = call.func.value
    if isinstance(receiver, ast.Name) and receiver.id in service_names:
        return True
    return any(
        isinstance(node, ast.Name) and node.id == "ModelCallService"
        for node in ast.walk(receiver)
    )


def _check_provider_bypass(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for relative, tree in _trees(root):
        if relative in PROVIDER_OWNERS:
            continue
        model_call_service_names = _model_call_service_names(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                method = _call_attribute(node)
                if method in {"complete", "stream"} and not _is_model_call_service_invocation(
                    node, model_call_service_names
                ):
                    violations.append(
                        _violation(
                            "W8-S1",
                            relative,
                            "provider lifecycle call bypasses ModelCallService",
                            node.lineno,
                        )
                    )
    return violations


def _check_plan_validator(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    owner = "agent/planning/plan_admission.py"
    for relative, tree in _trees(root):
        if relative == owner:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "PlanValidator":
                violations.append(
                    _violation("W8-S2", relative, "PlanValidator construction is outside PlanAdmission", node.lineno)
                )
    return violations


def _check_raw_error_policy(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    allowed = frozenset({"agent/runtime/failure_policy.py", "agent/runtime/failures.py"})
    for relative, tree in _trees(root):
        if relative in allowed:
            continue
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _is_policy_boundary(function):
                continue
            aliases = _text_aliases(function)
            for node in ast.walk(function):
                if isinstance(node, ast.Compare) and _has_text_policy_comparison(node, aliases):
                    violations.append(
                        _violation(
                            "W8-S3",
                            relative,
                            "human error/diagnostic text is used as retry or failure policy",
                            node.lineno,
                        )
                    )
                elif isinstance(node, ast.Call) and _has_text_policy_call(node, aliases):
                    violations.append(
                        _violation(
                            "W8-S3",
                            relative,
                            "human error/diagnostic text is used as retry or failure policy",
                            node.lineno,
                        )
                    )
    return violations


def _event_surface_violation(relative: str, node: ast.AST) -> ArchitectureViolation | None:
    if isinstance(node, ast.Name):
        name = node.id
    elif isinstance(node, ast.arg):
        name = node.arg
    elif isinstance(node, ast.Attribute):
        name = node.attr
    else:
        return None
    if name in {"event_emitter", "LegacyEventSinkAdapter"}:
        return _violation("W8-S4", relative, f"retired alternate event surface remains: {name}", node.lineno)
    return None


def _event_call_violation(relative: str, node: ast.AST) -> ArchitectureViolation | None:
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name) and node.func.id == "RuntimeEvent":
        if relative != "agent/runtime/events.py":
            return _violation(
                "W8-S4", relative, "RuntimeEvent is constructed outside its envelope owner", node.lineno
            )
    if isinstance(node.func, ast.Name) and node.func.id == "append_state_event":
        if relative != "agent/runtime/event_dispatch.py":
            return _violation(
                "W8-S4", relative, "state event append bypasses RuntimeEventDispatcher", node.lineno
            )
    if isinstance(node.func, ast.Attribute) and node.func.attr == "append":
        value = node.func.value
        if isinstance(value, ast.Attribute) and value.attr == "events":
            if relative not in {"agent/state.py", "agent/runtime/event_dispatch.py"}:
                return _violation(
                    "W8-S4", relative, "runtime event list is appended outside its owner", node.lineno
                )
    return None


def _check_event_and_correlation_ownership(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for relative, tree in _trees(root):
        for node in ast.walk(tree):
            for finding in (_event_surface_violation(relative, node), _event_call_violation(relative, node)):
                if finding is not None:
                    violations.append(finding)
    return violations


def _check_atomic_publication(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for relative, tree in _trees(root):
        if relative in ATOMIC_OWNERS:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _qualified_call(node, "os", frozenset({"replace", "fsync"})):
                violations.append(
                    _violation("W8-S5", relative, "durable atomic primitive is outside an approved owner", node.lineno)
                )
            if _qualified_call(node, "tempfile", frozenset({"mkstemp", "NamedTemporaryFile"})):
                violations.append(
                    _violation("W8-S5", relative, "durable temporary publication primitive is outside an approved owner", node.lineno)
                )
            if isinstance(node.func, ast.Name) and node.func.id in {"mkstemp", "NamedTemporaryFile"}:
                violations.append(
                    _violation("W8-S5", relative, "durable temporary publication primitive is outside an approved owner", node.lineno)
                )
    return violations


def _check_process_lifecycle(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    names = frozenset({"Popen", "run", "call", "check_call", "check_output"})
    for relative, tree in _trees(root):
        if relative in PROCESS_OWNERS:
            continue
        imported = _imported_names(tree, "subprocess")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            direct = _qualified_call(node, "subprocess", names)
            imported_call = isinstance(node.func, ast.Name) and node.func.id in imported
            os_process = _qualified_call(node, "os", frozenset({"system", "popen"}))
            if direct or imported_call or os_process:
                violations.append(
                    _violation("W8-S6", relative, "process lifecycle call is outside an approved owner", node.lineno)
                )
    return violations


def _path_join_expression(node: ast.AST) -> bool:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return True
    return isinstance(node, ast.Call) and _call_attribute(node) == "join"


def _assignment_targets(assignment: ast.Assign | ast.AnnAssign) -> list[ast.expr]:
    return assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]


def _direct_path_resolution(node: ast.AST, joined_names: set[str]) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "resolve":
        return False
    receiver = node.func.value
    return _path_join_expression(receiver) or (
        isinstance(receiver, ast.Name) and receiver.id in joined_names
    )


def _direct_path_constructor(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Path"


def _joined_path_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for assignment in ast.walk(function):
        if not isinstance(assignment, (ast.Assign, ast.AnnAssign)):
            continue
        if assignment.value is None or not _path_join_expression(assignment.value):
            continue
        names.update(
            target.id
            for target in _assignment_targets(assignment)
            if isinstance(target, ast.Name)
        )
    return names


def _resolved_path_names(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    joined_names: set[str],
) -> set[str]:
    names: set[str] = set()
    for assignment in ast.walk(function):
        if not isinstance(assignment, (ast.Assign, ast.AnnAssign)):
            continue
        if assignment.value is None or not _direct_path_resolution(assignment.value, joined_names):
            continue
        names.update(
            target.id
            for target in _assignment_targets(assignment)
            if isinstance(target, ast.Name)
        )
    return names


def _resolved_path_expression(
    node: ast.AST,
    resolved_names: set[str],
    joined_names: set[str],
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in resolved_names
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "resolve":
        return False
    receiver = node.func.value
    return (
        _path_join_expression(receiver)
        or (isinstance(receiver, ast.Name) and receiver.id in joined_names)
        or _direct_path_constructor(receiver)
    )


def _manual_confinement_call(function: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.Call | None:
    joined_names = _joined_path_names(function)
    resolved_names = _resolved_path_names(function, joined_names)
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"relative_to", "is_relative_to", "startswith"}:
            continue
        if _resolved_path_expression(node.func.value, resolved_names, joined_names):
            return node
    return None


def _check_manual_confinement(root: Path) -> list[ArchitectureViolation]:
    """Reject bounded reimplementations of the canonical path primitive."""

    violations: list[ArchitectureViolation] = []
    for relative, tree in _trees(root):
        if relative in PATH_OWNERS:
            continue
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            call = _manual_confinement_call(function)
            if call is not None:
                violations.append(
                    _violation(
                        "W8-S7",
                        relative,
                        "manual workspace confinement is outside the canonical path owner",
                        call.lineno,
                    )
                )
    return violations


def _profile_receiver(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in {"config", "model_profile", "profile", "values"}
    if isinstance(node, ast.Attribute):
        return node.attr in {"config", "model_profile", "profile"}
    return False


def _check_raw_profile_selection(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for relative, tree in _trees(root):
        if relative in PROFILE_OWNERS:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Call, ast.Subscript)):
                continue
            receiver = node.func.value if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) else node.value if isinstance(node, ast.Subscript) else None
            if receiver is None or not _profile_receiver(receiver):
                continue
            key = _mapping_key(node)
            if key in PROFILE_KEYS:
                violations.append(
                    _violation("W8-S8", relative, f"raw model-profile selection reads {key!r}", node.lineno)
                )
    return violations


def _retired_node_violation(relative: str, node: ast.AST) -> ArchitectureViolation | None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in RETIRED_NAMES:
        return _violation("W8-S9", relative, f"W7 retired definition reappeared: {node.name}", node.lineno)
    if isinstance(node, ast.Name) and node.id in RETIRED_NAMES:
        if node.id not in {"event_emitter", "LegacyEventSinkAdapter"}:
            return _violation("W8-S9", relative, f"W7 retired name remains live: {node.id}", node.lineno)
    if isinstance(node, ast.Attribute) and node.attr in RETIRED_NAMES:
        return _violation("W8-S9", relative, f"W7 retired attribute remains live: {node.attr}", node.lineno)
    return None


def _retired_module_violations(root: Path) -> list[ArchitectureViolation]:
    return [
        _violation("W8-S9", relative, "W7 retired module is present")
        for relative in RETIRED_MODULES
        if (root / relative).exists()
    ]


def _deferred_ledger_violations(root: Path) -> list[ArchitectureViolation]:
    ledger = _source(root, "scripts/compatibility_ledger.py") or ""
    if "_edge(" not in ledger or "DEFER_TO_W8_WITH_BLOCKING_EVIDENCE" not in ledger:
        return []
    return [
        _violation(
            "W8-S9",
            "scripts/compatibility_ledger.py",
            "completed W8 surface is still deferred",
            line_number,
        )
        for line_number, line in enumerate(ledger.splitlines(), start=1)
        if line.lstrip().startswith("_edge(") and "DEFER_TO_W8_WITH_BLOCKING_EVIDENCE" in line
    ]


def _check_w7_retirement(root: Path) -> list[ArchitectureViolation]:
    violations = _retired_module_violations(root)
    for relative, tree in _trees(root):
        for node in ast.walk(tree):
            finding = _retired_node_violation(relative, node)
            if finding is not None:
                violations.append(finding)
    violations.extend(_deferred_ledger_violations(root))
    return violations


def _check_removed_w8_fallbacks(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for relative, tree in _trees(root):
        if relative not in STDIO_BOUNDARY_FILES:
            source = _source(root, relative) or ""
            if "legacy_stdio_compatibility" in source:
                violations.append(_violation("W8-S10", relative, "stdio compatibility mode leaks outside its explicit boundary"))
        for node in _definitions(tree):
            if node.name in {"_call_extension", "legacy_reviewer"}:
                violations.append(_violation("W8-S10", relative, f"removed W8 fallback remains: {node.name}", node.lineno))
            if node.name == "resolve_user_path":
                for child in ast.walk(node):
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == "Path":
                        violations.append(_violation("W8-S10", relative, "resolve_user_path guesses a path from its argument", child.lineno))
    return violations


def _contains_name(node: ast.AST, name: str) -> bool:
    return any(isinstance(child, ast.Name) and child.id == name for child in ast.walk(node))


def _contains_literal(node: ast.AST, text: str) -> bool:
    return any(
        isinstance(child, ast.Constant)
        and isinstance(child.value, str)
        and text in child.value
        for child in ast.walk(node)
    )


def _is_path_cwd_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "cwd"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "Path"
    )


def _is_workspace_fallback(node: ast.AST) -> bool:
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        return len(node.values) > 1 and _contains_name(node, "workspace_paths")
    if isinstance(node, ast.IfExp):
        return _contains_name(node.test, "workspace_paths")
    return False


def _is_none_check(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.Is)
        and isinstance(node.left, ast.Name)
        and node.left.id == name
        and len(node.comparators) == 1
        and isinstance(node.comparators[0], ast.Constant)
        and node.comparators[0].value is None
    )


def _contains_raise(node: ast.AST) -> bool:
    return any(isinstance(child, ast.Raise) for child in ast.walk(node))


def _node_lineno(node: ast.AST) -> int | None:
    if isinstance(node, (ast.expr, ast.stmt)):
        return node.lineno
    return None


def _check_path_authority(root: Path) -> list[ArchitectureViolation]:
    """Reject productive path-authority fallbacks at their explicit owners."""

    violations: list[ArchitectureViolation] = []
    for relative in PATH_AUTHORITY_BOUNDARIES:
        tree = _tree(root, relative)
        if tree is None:
            continue
        for function in _definitions(tree):
            for node in ast.walk(function):
                if _contains_literal(node, ".test_runtime"):
                    violations.append(
                        _violation(
                            "W8-S13",
                            relative,
                            "productive path authority invents .test_runtime storage",
                            _node_lineno(node),
                        )
                    )
                    break
                if _is_path_cwd_call(node) and function.name not in EXPLICIT_CWD_SELECTION_FUNCTIONS:
                    violations.append(
                        _violation(
                            "W8-S13",
                            relative,
                            "productive path authority falls back to Path.cwd()",
                            _node_lineno(node),
                        )
                    )
                if _is_workspace_fallback(node):
                    violations.append(
                        _violation(
                            "W8-S13",
                            relative,
                            "missing WorkspacePaths follows an implicit fallback branch",
                            _node_lineno(node),
                        )
                    )
                if isinstance(node, ast.If) and any(
                    _is_none_check(node.test, name)
                    for name in ("workspace_paths", "workspace_root")
                ):
                    if not _contains_raise(node) and (
                        _contains_literal(node, ".test_runtime")
                        or any(
                            isinstance(child, ast.Call)
                            and isinstance(child.func, ast.Name)
                            and child.func.id in {"WorkspacePaths", "Path"}
                            for child in ast.walk(node)
                        )
                    ):
                        violations.append(
                            _violation(
                                "W8-S13",
                                relative,
                                "missing path authority is assigned a productive fallback",
                                node.lineno,
                            )
                        )

    return violations


def _check_reporting_reducers(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for relative, tree in _trees(root):
        if relative == "agent/evaluation/agent_executor.py":
            forbidden = {"project_run_metrics", "normalize_terminal_status", "project_operational_outcome", "build_canonical_run_snapshot"}
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id in forbidden:
                    violations.append(_violation("W8-S11", relative, f"evaluation reconstructs terminal truth with {node.id}", node.lineno))
                if isinstance(node, ast.Attribute) and node.attr in {"events", "last_result"}:
                    violations.append(_violation("W8-S11", relative, "evaluation reads live state instead of snapshot projection", node.lineno))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "project_run_metrics" and relative not in REPORTING_OWNERS:
                violations.append(_violation("W8-S11", relative, "run metrics are reduced outside the reporting owner", node.lineno))
            if isinstance(node.func, ast.Name) and node.func.id == "build_canonical_run_snapshot" and relative not in {"agent/application_result.py", "agent/reporting/run_snapshot.py"}:
                violations.append(_violation("W8-S11", relative, "canonical snapshot is built outside its owner", node.lineno))
    return violations


def _check_toolresult_mapping(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for relative, tree in _trees(root):
        if relative in RAW_RESULT_BOUNDARIES:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "get":
                continue
            receiver = node.func.value
            if isinstance(receiver, ast.Name) and receiver.id in {"result", "tool_result", "committed_result"}:
                violations.append(_violation("W8-S12", relative, "ToolResult mapping access is outside an explicit boundary", node.lineno))
    return violations


def _check_prior_gates(root: Path) -> list[ArchitectureViolation]:
    violations: list[ArchitectureViolation] = []
    for relative in PRIOR_CHECKERS:
        path = root / relative
        if not path.is_file():
            violations.append(_violation("W8-S12", relative, "required prior gate is missing"))
            continue
        try:
            result = subprocess.run(
                [sys.executable, str(path)],
                cwd=str(root),
                capture_output=True,
                text=True,
                # The prior W7 checker is source-complete and can take just
                # over 90 seconds on a constrained Windows runner.  This is
                # a bounded runner allowance, not a product or gate bypass:
                # failures and non-zero exits remain violations.
                timeout=150,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            violations.append(_violation("W8-S12", relative, f"prior gate could not run: {type(exc).__name__}"))
            continue
        if result.returncode != 0:
            output = (result.stdout or result.stderr or "prior gate failed").strip().splitlines()
            detail = output[0] if output else "prior gate failed"
            violations.append(_violation("W8-S12", relative, "prior gate failed: " + detail))
    return violations


_check_s1 = _check_provider_bypass
_check_s2 = _check_plan_validator
_check_s3 = _check_raw_error_policy
_check_s4 = _check_event_and_correlation_ownership
_check_s5 = _check_atomic_publication
_check_s6 = _check_process_lifecycle
_check_s7 = _check_manual_confinement
_check_s8 = _check_raw_profile_selection
_check_s9 = _check_w7_retirement
_check_s10 = _check_removed_w8_fallbacks
_check_s11 = _check_reporting_reducers
_check_s12 = _check_toolresult_mapping
_check_s13 = _check_path_authority


def check_source(path: str | Path, root: str | Path | None = None) -> list[ArchitectureViolation]:
    """Check one production source file without importing runtime modules."""

    source_path = Path(path).resolve()
    resolved_root = Path(root).resolve() if root is not None else ROOT
    try:
        relative = _relative(source_path, resolved_root)
    except ValueError:
        return [_violation("W8-S1", str(source_path), "source path is outside the repository root")]
    if _tree(resolved_root, relative) is None:
        return [_violation("W8-S1", relative, "source file is missing or unparsable")]
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
        _check_s10,
        _check_s11,
        _check_s12,
        _check_s13,
    )
    return [violation for check in checks for violation in check(resolved_root) if violation.path == relative]


def check_architecture(root: str | Path = ".") -> list[ArchitectureViolation]:
    """Return deterministic W8-S1..S12 violations."""

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
        _check_s10,
        _check_s11,
        _check_toolresult_mapping,
        _check_path_authority,
        _check_prior_gates,
    )
    return [violation for check in checks for violation in check(resolved)]


find_violations = check_architecture
check_wave8_architecture = check_architecture


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check W8 residual ownership architecture")
    parser.add_argument("root", nargs="?", default=".", help="repository root")
    args = parser.parse_args(list(argv) if argv is not None else None)
    violations = check_architecture(args.root)
    if violations:
        for violation in violations:
            print(violation.format())
        return 1
    print("W8 architecture checks: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
