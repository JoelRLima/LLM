"""Static ownership gates for Wave 2 failure and recovery truth.

The checker intentionally inspects syntax and a small amount of local alias
flow.  It is not a type checker: it catches the short, high-risk bypasses
that would otherwise reintroduce text policy or Mapping-style access after a
typed ``ToolResult`` has entered a canonical path.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "agent"

# These modules are named compatibility/projection edges.  They may inspect a
# mapping because their contract is explicitly to translate or project one;
# canonical policy code is not allowed to join this set merely to silence a
# finding.
MAPPING_COMPATIBILITY_FILES = frozenset(
    {
        "agent/tools/contracts.py",
        "agent/tools/result_adapter.py",
        "agent/planning/deferred_execution.py",
        "agent/planning/observation_invalidation.py",
        "agent/planning/provenance_validation.py",
        "agent/planning/result_bindings.py",
        "agent/planning/task_semantics_admission.py",
        "agent/planning/task_semantics_evidence.py",
        "agent/planning/task_semantics_terminal.py",
        "agent/planning/task_semantics_transitions.py",
        "agent/runtime/failure_policy.py",
        "agent/runtime/operational_outcome_evidence.py",
        "agent/tools/invocation_quiescence.py",
    }
)

# A serialized pre-Wave-2 replan context is still accepted at this one named
# edge.  The edge may recognize only its documented wire-format prefix; it is
# not a general failure classifier.
TEXT_COMPATIBILITY_FILES = frozenset({"agent/planning/replan_compat.py"})

# Code-assistance diagnostics classify editor/test text for user guidance;
# they do not decide runtime recovery and are outside CEN-05's runtime core.
TEXT_POLICY_OUT_OF_SCOPE_FILES = frozenset({"agent/code/diagnostics.py"})

RECOVERY_COMPATIBILITY_FILES = frozenset(
    {
        "agent/state.py",
        "agent/state_checkpoint.py",
        "agent/state_checkpoint_counters.py",
        "agent/state_progression.py",
        "agent/planning/reasoning_boundary.py",
        "agent/planning/task_completion.py",
    }
)
RECOVERY_OWNER_FILES = frozenset({"agent/runtime/recovery.py"})
REPLAN_COMPATIBILITY_FILES = frozenset({"agent/planning/replan_compat.py"})
GENERAL_LIMIT_FILES = frozenset(
    {
        "agent/code/workflows.py",
        "agent/runtime/context.py",
        "agent/runtime/hardware.py",
        "agent/runtime/instance_lock.py",
        "agent/runtime/limits.py",
    }
)

OLD_RECOVERY_COUNTERS = frozenset(
    {"replan_counts", "continuation_attempts", "reasoning_turns_used"}
)
RECOVERY_METHODS = frozenset(
    {"try_consume", "can_attempt", "used", "remaining", "limit"}
)
RECOVERY_MUTATION_METHODS = frozenset(
    {"clear", "pop", "popitem", "setdefault", "update"}
)
RECOVERY_TABLE_NAMES = frozenset(
    {
        "RECOVERY_LIMITS",
        "RECOVERY_POLICY_LIMITS",
        "REPAIR_LIMITS",
        "REPLAN_LIMITS",
        "RETRY_LIMITS",
        "RETRY_POLICY_LIMITS",
        "CONTINUATION_LIMITS",
    }
)

TOOL_RESULT_FIELDS = frozenset(
    {
        "status",
        "ok",
        "done",
        "data",
        "error",
        "message",
        "metadata",
        "artifacts",
        "executed",
        "evidence_provenance",
        "done_override",
    }
)

TEXT_METHODS = frozenset({"lower", "casefold", "startswith", "endswith"})
LEGACY_RESULT_METHODS = frozenset({"to_legacy_dict"})
MAPPING_ACCESS_METHODS = frozenset({"get", "__getitem__"})
TEXT_NAMES = frozenset(
    {
        "error",
        "error_message",
        "failure",
        "failure_message",
        "message",
        "reason",
    }
)
POLICY_NAME_PARTS = (
    "classif",
    "retry",
    "replan",
    "recover",
    "policy",
    "handle_step_failure",
    "should_",
    "can_",
)
RENDER_NAME_PARTS = ("sanitize", "render", "format", "public", "prompt")


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _files() -> Iterable[Path]:
    yield from sorted(AGENT_ROOT.rglob("*.py"))


def _literal_string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _assigned_names(node: ast.AST | None) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for item in node.elts:
            names.update(_assigned_names(item))
        return names
    return set()


def _attribute_parts(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return tuple(reversed(parts))


def _annotation_has_tool_result(annotation: ast.AST | None, aliases: set[str]) -> bool:
    if annotation is None:
        return False
    if isinstance(annotation, ast.Name):
        return annotation.id in aliases
    if isinstance(annotation, ast.Attribute):
        return annotation.attr == "ToolResult"
    return any(_annotation_has_tool_result(child, aliases) for child in ast.iter_child_nodes(annotation))


def _annotation_has_mapping(annotation: ast.AST | None) -> bool:
    if annotation is None:
        return False
    if isinstance(annotation, ast.Name):
        return annotation.id in {"Mapping", "MutableMapping", "dict", "Dict"}
    if isinstance(annotation, ast.Attribute):
        return annotation.attr in {"Mapping", "MutableMapping", "dict", "Dict"}
    return any(_annotation_has_mapping(child) for child in ast.iter_child_nodes(annotation))


def _tool_result_import_aliases(tree: ast.AST) -> set[str]:
    aliases = {"ToolResult"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != "agent.tools.contracts":
            continue
        for imported in node.names:
            if imported.name == "ToolResult":
                aliases.add(imported.asname or imported.name)
    return aliases


def _declared_typed_names(tree: ast.AST, aliases: set[str]) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg):
            if _annotation_has_tool_result(node.annotation, aliases) and not _annotation_has_mapping(node.annotation):
                names.add(node.arg)
        elif isinstance(node, ast.AnnAssign):
            if _annotation_has_tool_result(node.annotation, aliases) and not _annotation_has_mapping(node.annotation):
                names.update(_assigned_names(node.target))
    return names


def _assignment_parts(node: ast.AST) -> tuple[ast.expr | None, list[ast.expr]]:
    if isinstance(node, ast.Assign):
        return node.value, node.targets
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return node.value, [node.target]
    return None, []


def _typed_value_source(value: ast.AST, names: set[str]) -> bool:
    if isinstance(value, ast.Name) and value.id in names:
        return True
    if not isinstance(value, ast.Call):
        return False
    parts = _attribute_parts(value.func) if isinstance(value.func, ast.Attribute) else ()
    return (isinstance(value.func, ast.Name) and value.func.id == "ensure_canonical_result") or (
        "ensure_canonical_result" in parts
    )


def _propagate_typed_names(tree: ast.AST, names: set[str]) -> None:
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            value, targets = _assignment_parts(node)
            if value is None:
                continue
            if not _typed_value_source(value, names):
                continue
            for target in targets:
                new_names = _assigned_names(target) - names
                if new_names:
                    names.update(new_names)
                    changed = True


def _typed_names(tree: ast.AST, aliases: set[str] | None = None) -> set[str]:
    names = _declared_typed_names(
        tree, aliases or _tool_result_import_aliases(tree)
    )
    _propagate_typed_names(tree, names)
    return names


def _is_typed_receiver(node: ast.AST, names: set[str]) -> bool:
    return isinstance(node, ast.Name) and node.id in names


def _literal_getattr(node: ast.Call) -> tuple[ast.AST, str] | None:
    if not isinstance(node.func, ast.Name) or node.func.id != "getattr" or len(node.args) < 2:
        return None
    method = _literal_string(node.args[1])
    return (node.args[0], method) if method is not None else None


def _mapping_receiver(
    node: ast.AST, typed_names: set[str], mapping_names: set[str]
) -> bool:
    return isinstance(node, ast.Name) and (
        node.id in typed_names or node.id in mapping_names
    )


def _literal_method_call(node: ast.AST) -> tuple[ast.AST, str] | None:
    if not isinstance(node, ast.Call):
        return None
    return _literal_getattr(node)


def _mapping_value_source(
    value: ast.AST,
    typed_names: set[str],
    mapping_names: set[str],
    conversion_aliases: set[str],
) -> bool:
    if isinstance(value, ast.Name):
        return value.id in mapping_names
    return _mapping_expression(
        value, typed_names, mapping_names, conversion_aliases
    )


def _mapping_expression(
    node: ast.AST,
    typed_names: set[str],
    mapping_names: set[str],
    conversion_aliases: set[str],
) -> bool:
    """Recognize only the bounded mapping expressions used by ToolResult edges."""

    if _mapping_receiver(node, typed_names, mapping_names):
        return True
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name):
        if node.func.id in conversion_aliases:
            return True
        if node.func.id == "dict":
            return any(
                _mapping_expression(argument, typed_names, mapping_names, conversion_aliases)
                for argument in node.args
            )
        return False
    if isinstance(node.func, ast.Attribute) and node.func.attr in LEGACY_RESULT_METHODS:
        return _mapping_expression(
            node.func.value, typed_names, mapping_names, conversion_aliases
        )
    literal_method = _literal_method_call(node.func)
    return bool(
        literal_method
        and literal_method[1] in LEGACY_RESULT_METHODS
        and _mapping_expression(
            literal_method[0], typed_names, mapping_names, conversion_aliases
        )
    )


def _mapping_method_source(
    value: ast.AST,
    typed_names: set[str],
    mapping_names: set[str],
    method_aliases: set[str],
) -> bool:
    if isinstance(value, ast.Name):
        return value.id in method_aliases
    if isinstance(value, ast.Attribute) and value.attr in MAPPING_ACCESS_METHODS:
        return _mapping_receiver(value.value, typed_names, mapping_names)
    literal_method = _literal_method_call(value)
    return bool(
        literal_method
        and literal_method[1] in MAPPING_ACCESS_METHODS
        and _mapping_receiver(literal_method[0], typed_names, mapping_names)
    )


def _mapping_provenance(
    tree: ast.AST, typed_names: set[str]
) -> tuple[set[str], set[str], set[str]]:
    mapping_names: set[str] = set()
    method_aliases: set[str] = set()
    conversion_aliases: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            value, targets = _assignment_parts(node)
            if value is None:
                continue
            assigned = set().union(*(_assigned_names(target) for target in targets))
            if _mapping_value_source(
                value, typed_names, mapping_names, conversion_aliases
            ):
                new_names = assigned - mapping_names
                if new_names:
                    mapping_names.update(new_names)
                    changed = True
            if _mapping_method_source(
                value, typed_names, mapping_names, method_aliases
            ):
                new_names = assigned - method_aliases
                if new_names:
                    method_aliases.update(new_names)
                    changed = True
            if _conversion_method_source(
                value, typed_names, mapping_names, conversion_aliases
            ):
                new_names = assigned - conversion_aliases
                if new_names:
                    conversion_aliases.update(new_names)
                    changed = True
    return mapping_names, method_aliases, conversion_aliases


def _conversion_method_source(
    value: ast.AST,
    typed_names: set[str],
    mapping_names: set[str],
    conversion_aliases: set[str],
) -> bool:
    if isinstance(value, ast.Name):
        return value.id in conversion_aliases
    if isinstance(value, ast.Attribute) and value.attr in LEGACY_RESULT_METHODS:
        return _mapping_receiver(value.value, typed_names, mapping_names)
    literal_method = _literal_method_call(value)
    return bool(
        literal_method
        and literal_method[1] in LEGACY_RESULT_METHODS
        and _mapping_receiver(literal_method[0], typed_names, mapping_names)
    )


def _attribute_mapping_access(
    node: ast.Call,
    typed_names: set[str],
    mapping_names: set[str],
    conversion_aliases: set[str],
) -> bool:
    return bool(
        isinstance(node.func, ast.Attribute)
        and node.func.attr in MAPPING_ACCESS_METHODS
        and node.args
        and _mapping_expression(
            node.func.value, typed_names, mapping_names, conversion_aliases
        )
    )


def _literal_mapping_access(
    node: ast.Call,
    typed_names: set[str],
    mapping_names: set[str],
    conversion_aliases: set[str],
) -> bool:
    getattr_value = _literal_getattr(node)
    if getattr_value is None:
        getattr_value = _literal_method_call(node.func)
    if getattr_value is None:
        return False
    receiver, method = getattr_value
    if not _mapping_expression(
        receiver, typed_names, mapping_names, conversion_aliases
    ):
        return False
    if method in MAPPING_ACCESS_METHODS:
        return True
    return (
        isinstance(receiver, ast.Name)
        and receiver.id in mapping_names
        and method in TOOL_RESULT_FIELDS
    )


def _bound_mapping_access(node: ast.Call, method_aliases: set[str]) -> bool:
    return bool(
        isinstance(node.func, ast.Name) and node.func.id in method_aliases
    )


def _mapping_access(
    node: ast.AST,
    typed_names: set[str],
    mapping_names: set[str],
    method_aliases: set[str],
    conversion_aliases: set[str],
) -> bool:
    if isinstance(node, ast.Call):
        return (
            _attribute_mapping_access(
                node, typed_names, mapping_names, conversion_aliases
            )
            or _literal_mapping_access(
                node, typed_names, mapping_names, conversion_aliases
            )
            or _bound_mapping_access(node, method_aliases)
        )
    if isinstance(node, ast.Subscript) and _mapping_expression(
        node.value, typed_names, mapping_names, conversion_aliases
    ):
        return True
    return False


def _text_receiver(node: ast.AST, aliases: set[str] | None = None) -> bool:
    if isinstance(node, ast.Name):
        return node.id in (aliases or set()) or node.id.casefold() in TEXT_NAMES or any(
            token in node.id.casefold() for token in ("error", "failure", "reason")
        )
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str":
        return bool(node.args) and _text_receiver(node.args[0], aliases)
    return False


def _text_operation(node: ast.AST, text_method_aliases: set[str]) -> bool:
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr in TEXT_METHODS:
            return _text_receiver(node.func.value, text_method_aliases)
        getattr_value = _literal_getattr(node)
        if getattr_value is not None:
            receiver, method = getattr_value
            return _text_receiver(receiver, text_method_aliases) and method in TEXT_METHODS
        if isinstance(node.func, ast.Name) and node.func.id in text_method_aliases:
            return True
    return False


def _text_compare(node: ast.Compare, text_method_aliases: set[str]) -> bool:
    values = [node.left, *node.comparators]
    if any(_text_operation(value, text_method_aliases) for value in values):
        return True
    has_text = any(_text_receiver(value, text_method_aliases) for value in values)
    has_literal = any(isinstance(value, ast.Constant) and isinstance(value.value, str) for value in values)
    return has_text and has_literal


def _regex_on_text(node: ast.Call, text_aliases: set[str]) -> bool:
    if not isinstance(node.func, ast.Attribute) or node.func.attr not in {"search", "match", "fullmatch"}:
        return False
    if not isinstance(node.func.value, ast.Name) or node.func.value.id != "re":
        return False
    return any(_text_receiver(argument, text_aliases) for argument in node.args[1:])


def _old_counter_access(node: ast.AST, aliases: set[str] | None = None) -> bool:
    if isinstance(node, ast.Name) and aliases and node.id in aliases:
        return True
    if isinstance(node, ast.Attribute):
        return node.attr in OLD_RECOVERY_COUNTERS
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
        return node.value.attr in OLD_RECOVERY_COUNTERS
    return False


def _legacy_counter_aliases(tree: ast.AST) -> set[str]:
    aliases: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if value is None or not _is_legacy_counter_source(value, aliases):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                new_aliases = _assigned_names(target) - aliases
                if new_aliases:
                    aliases.update(new_aliases)
                    changed = True
    return aliases


def _is_legacy_counter_source(
    node: ast.AST, aliases: set[str] | None = None
) -> bool:
    if _old_counter_access(node, aliases):
        return True
    if isinstance(node, ast.Call):
        getattr_value = _literal_getattr(node)
        return getattr_value is not None and getattr_value[1] in OLD_RECOVERY_COUNTERS
    return False


def _old_counter_mutation(
    node: ast.AST, aliases: set[str] | None = None
) -> bool:
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if value is None:
            return False
        source_is_counter = _is_legacy_counter_source(value, aliases)
        return any(
            _old_counter_access(target, aliases)
            and not (
                isinstance(target, ast.Name)
                and source_is_counter
                and aliases
                and target.id in aliases
            )
            for target in targets
        )
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return (
            node.func.attr in RECOVERY_MUTATION_METHODS
            and _old_counter_access(node.func.value, aliases)
        )
    return False


def _repair_budget_access(node: ast.AST, aliases: set[str] | None = None) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "repair_budget" or bool(aliases and node.id in aliases)
    if isinstance(node, ast.Attribute):
        return node.attr == "repair_budget"
    if isinstance(node, ast.Subscript):
        return _repair_budget_access(node.value, aliases)
    return False


def _repair_budget_aliases(tree: ast.AST) -> set[str]:
    aliases: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if value is None or not _repair_budget_access(value, aliases):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                new_aliases = _assigned_names(target) - aliases
                if new_aliases:
                    aliases.update(new_aliases)
                    changed = True
    return aliases


def _repair_budget_mutation(
    node: ast.AST, aliases: set[str] | None = None
) -> bool:
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if value is None:
            return False
        source_is_budget = _repair_budget_access(value, aliases)
        for target in targets:
            if _repair_budget_access(target, aliases):
                if (
                    isinstance(target, ast.Name)
                    and source_is_budget
                    and aliases
                    and target.id in aliases
                ):
                    continue
                return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return (
            node.func.attr in RECOVERY_MUTATION_METHODS
            and _repair_budget_access(node.func.value, aliases)
        )
    return False


def _raw_scope_key(node: ast.Call) -> bool:
    if (
        not isinstance(node, ast.Call)
        or not isinstance(node.func, ast.Attribute)
        or node.func.attr not in RECOVERY_METHODS
    ):
        return False
    receiver = node.func.value
    if isinstance(receiver, ast.Name):
        is_budget = "budget" in receiver.id.casefold()
    elif isinstance(receiver, ast.Attribute):
        is_budget = receiver.attr == "recovery_budget" or "budget" in receiver.attr.casefold()
    else:
        is_budget = False
    scope_values = list(node.args[:1])
    scope_values.extend(
        keyword.value
        for keyword in node.keywords
        if keyword.arg in {"scope", "recovery_scope"}
    )
    return is_budget and any(
        isinstance(value, ast.Constant) and isinstance(value.value, str)
        for value in scope_values
    )


def _numeric_recovery_constant(node: ast.Assign | ast.AnnAssign) -> bool:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        if not isinstance(target, ast.Name):
            continue
        name = target.id.casefold()
        if (
            name.startswith("max_retry")
            or any(
                token in name and (token != "continu" or "continuity" not in name)
                for token in ("replan", "repair", "continu", "recover")
            )
        ) and isinstance(
            node.value, ast.Constant
        ) and isinstance(node.value.value, (int, float)) and not isinstance(node.value.value, bool):
            return True
    return False


def _numeric_recovery_default(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    positional = [*node.args.posonlyargs, *node.args.args]
    positional_defaults: list[ast.expr | None] = [None] * (
        len(positional) - len(node.args.defaults)
    )
    positional_defaults.extend(node.args.defaults)
    candidates = list(zip(positional, positional_defaults, strict=True))
    candidates.extend(
        (argument, default)
        for argument, default in zip(
            node.args.kwonlyargs, node.args.kw_defaults, strict=True
        )
    )
    for argument, default in candidates:
        name = argument.arg.casefold()
        if (
            default is not None
            and isinstance(default, ast.Constant)
            and isinstance(default.value, (int, float))
            and not isinstance(default.value, bool)
            and (
                name.startswith("max_retry")
                or any(
                    token in name and (token != "continu" or "continuity" not in name)
                    for token in ("replan", "repair", "continu", "recover")
                )
            )
        ):
            return True
    return False


def _recovery_table_assignment(node: ast.Assign | ast.AnnAssign) -> bool:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return any(
        isinstance(target, ast.Name)
        and target.id.upper() in RECOVERY_TABLE_NAMES
        for target in targets
    )


def _alternate_recovery_class(name: str) -> bool:
    lowered = name.casefold()
    return lowered.endswith(("retrypolicy", "recoverypolicy", "recoverybudgetstate"))


def _policy_function(name: str) -> bool:
    lowered = name.casefold()
    return any(part in lowered for part in POLICY_NAME_PARTS) and not any(
        part in lowered for part in RENDER_NAME_PARTS
    )


def _classification_finding(node: ast.AST, relative: str) -> str | None:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "classify_error":
        return f"S2-1: {relative}:{node.lineno} legacy classify_error policy owner"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "classify_error":
        return f"S2-1: {relative}:{node.lineno} direct classify_error policy call"
    return None


def _class_owner_finding(node: ast.AST, relative: str) -> str | None:
    if not isinstance(node, ast.ClassDef):
        return None
    lowered = node.name.casefold()
    is_policy = node.name in {"RetryPolicy", "RecoveryPolicy", "RecoveryBudgetState"}
    if not is_policy and not _alternate_recovery_class(node.name):
        return None
    if lowered.endswith("retrypolicy") and relative not in REPLAN_COMPATIBILITY_FILES:
        return f"S2-4: {relative}:{node.lineno} alternate recovery owner/class"
    if lowered.endswith(("recoverypolicy", "recoverybudgetstate")) and relative not in RECOVERY_OWNER_FILES:
        return f"S2-4: {relative}:{node.lineno} alternate recovery owner/class"
    return None


def _legacy_ownership_findings(
    node: ast.AST,
    relative: str,
    legacy_counter_aliases: set[str],
    repair_budget_aliases: set[str],
) -> list[str]:
    findings: list[str] = []
    lineno = getattr(node, "lineno", 0)
    if relative not in RECOVERY_COMPATIBILITY_FILES and _old_counter_mutation(
        node, legacy_counter_aliases
    ):
        findings.append(
            f"S2-3: {relative}:{lineno} direct mutation of legacy recovery counter"
        )
    if _repair_budget_mutation(node, repair_budget_aliases):
        findings.append(f"S2-3: {relative}:{lineno} mutable repair_budget ownership")
    if (
        relative not in RECOVERY_OWNER_FILES
        and isinstance(node, ast.Call)
        and _raw_scope_key(node)
    ):
        findings.append(f"S2-4: {relative}:{lineno} raw recovery scope key")
    return findings


def _limit_ownership_findings(node: ast.AST, relative: str) -> list[str]:
    allowed = (
        relative in RECOVERY_OWNER_FILES
        or relative in GENERAL_LIMIT_FILES
        or relative in REPLAN_COMPATIBILITY_FILES
    )
    if allowed:
        return []
    lineno = getattr(node, "lineno", 0)
    findings: list[str] = []
    if isinstance(node, (ast.Assign, ast.AnnAssign)) and (
        _numeric_recovery_constant(node) or _recovery_table_assignment(node)
    ):
        findings.append(f"S2-3: {relative}:{lineno} local recovery-limit authority")
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _numeric_recovery_default(node):
        findings.append(f"S2-3: {relative}:{lineno} local recovery-limit default")
    return findings


def _construction_findings(node: ast.AST, relative: str) -> list[str]:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        return []
    findings: list[str] = []
    lineno = getattr(node, "lineno", 0)
    if node.func.id == "RetryPolicy" and relative not in REPLAN_COMPATIBILITY_FILES:
        findings.append(f"S2-3: {relative}:{lineno} local RetryPolicy construction")
    if (
        node.func.id in {"RecoveryPolicy", "RecoveryBudgetState"}
        and relative not in RECOVERY_OWNER_FILES
        and relative != "agent/state.py"
    ):
        findings.append(f"S2-4: {relative}:{lineno} alternate recovery owner construction")
    return findings


def _ownership_findings(tree: ast.AST, relative: str) -> list[str]:
    findings: list[str] = []
    legacy_counter_aliases = _legacy_counter_aliases(tree)
    repair_budget_aliases = _repair_budget_aliases(tree)
    for node in ast.walk(tree):
        for finding in (
            _classification_finding(node, relative),
            _class_owner_finding(node, relative),
        ):
            if finding:
                findings.append(finding)
        findings.extend(
            _legacy_ownership_findings(
                node, relative, legacy_counter_aliases, repair_budget_aliases
            )
        )
        findings.extend(_limit_ownership_findings(node, relative))
        findings.extend(_construction_findings(node, relative))
    return findings


def _text_aliases(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    aliases: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(function):
            value, targets = _assignment_parts(node)
            if value is None:
                continue
            source = _text_receiver(value, aliases) or _text_operation(value, aliases)
            if isinstance(value, ast.Attribute) and value.attr in TEXT_METHODS:
                source = source or _text_receiver(value.value, aliases)
            if source:
                for target in targets:
                    new_aliases = _assigned_names(target) - aliases
                    if new_aliases:
                        aliases.update(new_aliases)
                        changed = True
    return aliases


def _text_policy_findings(
    function: ast.FunctionDef | ast.AsyncFunctionDef, relative: str
) -> list[str]:
    aliases = _text_aliases(function)
    findings: list[str] = []
    for node in ast.walk(function):
        if isinstance(node, ast.Compare) and _text_compare(node, aliases):
            findings.append(
                f"S2-1: {relative}:{node.lineno} human-text failure policy classification"
            )
        if isinstance(node, ast.Call) and (
            _regex_on_text(node, aliases) or _text_operation(node, aliases)
        ):
            findings.append(
                f"S2-1: {relative}:{node.lineno} human-text failure policy classification"
            )
    return findings


def _text_findings(tree: ast.AST, relative: str) -> list[str]:
    if relative in TEXT_COMPATIBILITY_FILES or relative in TEXT_POLICY_OUT_OF_SCOPE_FILES:
        return []
    findings: list[str] = []
    for function in (
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        if _policy_function(function.name):
            findings.extend(_text_policy_findings(function, relative))
    return findings


def _mapping_findings(tree: ast.AST, relative: str) -> list[str]:
    if relative in MAPPING_COMPATIBILITY_FILES:
        return []
    imported_aliases = _tool_result_import_aliases(tree)
    findings: list[str] = []
    for function in (
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        scoped_typed_names = _typed_names(function, imported_aliases)
        (
            scoped_mapping_names,
            scoped_method_aliases,
            scoped_conversion_aliases,
        ) = _mapping_provenance(
            function, scoped_typed_names
        )
        for node in ast.walk(function):
            if _mapping_access(
                node,
                scoped_typed_names,
                scoped_mapping_names,
                scoped_method_aliases,
                scoped_conversion_aliases,
            ):
                findings.append(
                    f"S2-2: {relative}:{getattr(node, 'lineno', 0)} Mapping-style access on typed ToolResult"
                )
    return findings


def _check_tree(tree: ast.AST, relative: str) -> list[str]:
    findings = _ownership_findings(tree, relative)
    findings.extend(_text_findings(tree, relative))
    findings.extend(_mapping_findings(tree, relative))
    return sorted(set(findings))


def check_source(source: str, relative: str = "<source>") -> list[str]:
    """Check a source snippet with the same gates used for repository files."""

    return _check_tree(ast.parse(textwrap.dedent(source), filename=relative), relative)


def _check_file(path: Path) -> list[str]:
    relative = _relative(path)
    return _check_tree(ast.parse(path.read_text(encoding="utf-8"), filename=relative), relative)


def run_checks() -> list[str]:
    findings: list[str] = []
    for path in _files():
        findings.extend(_check_file(path))
    return sorted(set(findings))


def main() -> int:
    findings = run_checks()
    if findings:
        print("Wave 2 architecture gates failed:")
        print("\n".join(f"- {finding}" for finding in findings))
        return 1
    print("Wave 2 architecture gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
