"""Static ownership gates for the Wave 1 model/measurement truth spine."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "agent"

# These are explicit semantic boundaries, not a blanket grep allowlist.  The
# provider implementation owns transport, the runtime service owns lifecycle,
# and evaluation's RecordingGateway is observational by contract.
GATEWAY_OWNER_PREFIXES = ("agent/llm/providers/",)
GATEWAY_OWNER_FILES = {
    "agent/runtime/model_call.py",
    "agent/runtime/model_call_stream.py",
    "agent/evaluation/trace.py",
}
PROFILE_INPUT_FILES = {
    "agent/llm/model_profile.py",
    "agent/llm/model_profile_compat.py",
    "agent/runtime/config_effective.py",
    "agent/runtime/config_environment.py",
    "agent/runtime/config_schema.py",
    "agent/runtime/config_validation.py",
}
PROFILE_SELECTION_BOUNDARY_FILES = PROFILE_INPUT_FILES | {
    "agent/application.py",
    "agent/interfaces/cli/command_handlers.py",
}
CAPABILITY_INPUT_FILES = PROFILE_INPUT_FILES | {
    "agent/runtime/request_measurement.py",
}
METRIC_OWNER_FILES = {
    "agent/llm/model_metrics.py",
    "agent/runtime/model_call.py",
    "agent/runtime/model_call_record.py",
}

_GATEWAY_ROOT_NAMES = frozenset({"gateway", "model_gateway"})
_CAPABILITY_ROOT_NAMES = frozenset({"capabilities", "provider_capabilities"})
_CAPABILITY_KEYS = frozenset(
    {
        "streaming",
        "structured_output",
        "structured_output_modes",
        "reasoning",
        "token_counting",
        "tool_calls",
    }
)
_GATEWAY_TYPE_NAMES = frozenset({"ModelGateway"})
_CONTEXT_TYPE_NAMES = frozenset({"TaskExecutionContext"})
_CONTEXT_ROOT_NAMES = frozenset({"context", "execution_context", "task_context"})


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _files() -> Iterable[Path]:
    yield from sorted(AGENT_ROOT.rglob("*.py"))


def _attribute_parts(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return tuple(reversed(parts))


def _literal_string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _literal_getattr_method(node: ast.AST, methods: set[str] | frozenset[str]) -> str | None:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        return None
    if node.func.id != "getattr" or len(node.args) < 2:
        return None
    method = _literal_string(node.args[1])
    return method if method in methods else None


def _assigned_names(node: ast.AST | None) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for item in node.elts:
            names.update(_assigned_names(item))
        return names
    return set()


def _imported_type_names(tree: ast.AST, module: str, names: frozenset[str]) -> set[str]:
    aliases = set(names)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != module:
            continue
        for imported in node.names:
            if imported.name in names:
                aliases.add(imported.asname or imported.name)
    return aliases


def _annotation_contains_name(annotation: ast.AST | None, names: set[str]) -> bool:
    if annotation is None:
        return False
    if isinstance(annotation, ast.Name):
        return annotation.id in names
    if isinstance(annotation, ast.Attribute):
        return annotation.attr in names
    return any(
        _annotation_contains_name(child, names)
        for child in ast.iter_child_nodes(annotation)
    )


def _annotated_names(tree: ast.AST, module: str, type_names: frozenset[str]) -> set[str]:
    imported_names = _imported_type_names(tree, module, type_names)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and _annotation_contains_name(node.annotation, imported_names):
            names.add(node.arg)
        elif isinstance(node, ast.AnnAssign) and _annotation_contains_name(
            node.annotation, imported_names
        ):
            names.update(_assigned_names(node.target))
    return names


def _gateway_source(node: ast.AST, aliases: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in aliases
    if isinstance(node, ast.Attribute):
        return node.attr.casefold() in _GATEWAY_ROOT_NAMES
    if isinstance(node, ast.Call):
        parts = _attribute_parts(node.func)
        return any(
            part.casefold() in _GATEWAY_ROOT_NAMES
            or part.casefold().endswith("_gateway")
            for part in parts
        )
    return False


def _collect_gateway_aliases(tree: ast.AST) -> set[str]:
    aliases = set(_GATEWAY_ROOT_NAMES)
    aliases.update(
        _annotated_names(tree, "agent.llm.contracts", _GATEWAY_TYPE_NAMES)
    )
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                if _gateway_source(node.value, aliases):
                    targets = node.targets
                else:
                    targets = []
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target] if node.value is not None and _gateway_source(node.value, aliases) else []
            elif isinstance(node, ast.NamedExpr):
                targets = [node.target] if _gateway_source(node.value, aliases) else []
            else:
                targets = []
            for target in targets:
                names = _assigned_names(target)
                if not names.issubset(aliases):
                    aliases.update(names)
                    changed = True
    return aliases


def _gateway_receiver(node: ast.AST, aliases: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in aliases
    if isinstance(node, ast.Attribute):
        return (
            node.attr.casefold() in _GATEWAY_ROOT_NAMES
            or _gateway_receiver(node.value, aliases)
        )
    return False


def _collect_bound_method_aliases(
    tree: ast.AST,
    source: Callable[[ast.AST, set[str], set[str]], bool],
    base_aliases: set[str],
) -> set[str]:
    aliases: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets if source(node.value, base_aliases, aliases) else []
            elif isinstance(node, ast.AnnAssign):
                targets = (
                    [node.target]
                    if node.value is not None and source(node.value, base_aliases, aliases)
                    else []
                )
            elif isinstance(node, ast.NamedExpr):
                targets = [node.target] if source(node.value, base_aliases, aliases) else []
            else:
                targets = []
            for target in targets:
                names = _assigned_names(target)
                if not names.issubset(aliases):
                    aliases.update(names)
                    changed = True
    return aliases


def _gateway_method_source(
    node: ast.AST,
    gateway_aliases: set[str],
    method_aliases: set[str],
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in method_aliases
    if isinstance(node, ast.Attribute):
        return node.attr in {"complete", "stream"} and _gateway_receiver(
            node.value, gateway_aliases
        )
    method = _literal_getattr_method(node, {"complete", "stream"})
    if method is not None and isinstance(node, ast.Call):
        return _gateway_receiver(node.args[0], gateway_aliases)
    return False


def _gateway_call(
    node: ast.Call,
    aliases: set[str],
    method_aliases: set[str],
) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in method_aliases
    method = _literal_getattr_method(node.func, {"complete", "stream"})
    if method is not None and isinstance(node.func, ast.Call):
        return _gateway_receiver(node.func.args[0], aliases)
    if not isinstance(node.func, ast.Attribute) or node.func.attr not in {"complete", "stream"}:
        return False
    return _gateway_receiver(node.func.value, aliases)


def _profile_selection(node: ast.AST) -> bool:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        key = _literal_string(node.args[0]) if node.args else None
        if node.func.attr == "get" and key in {"model_profiles", "default_model_profile"}:
            return True
    if isinstance(node, ast.Subscript):
        return _literal_string(node.slice) in {"model_profiles", "default_model_profile"}
    return False


def _capability_source(
    node: ast.AST,
    aliases: set[str],
    gateway_aliases: set[str],
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in aliases
    if isinstance(node, ast.Attribute):
        return (
            node.attr.casefold() == "capabilities"
            and _gateway_receiver(node.value, gateway_aliases)
        ) or _capability_source(
            node.value, aliases, gateway_aliases
        )
    if isinstance(node, ast.Subscript):
        return (
            _literal_string(node.slice) == "capabilities"
            or _capability_source(node.value, aliases, gateway_aliases)
        )
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return (
            node.func.attr == "get"
            and bool(node.args)
            and _literal_string(node.args[0]) == "capabilities"
        )
    method = _literal_getattr_method(node, {"capabilities"})
    if method is not None and isinstance(node, ast.Call):
        return _gateway_receiver(node.args[0], gateway_aliases)
    return False


def _collect_capability_aliases(
    tree: ast.AST,
    gateway_aliases: set[str],
) -> set[str]:
    aliases = set(_CAPABILITY_ROOT_NAMES)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                if _capability_source(node.value, aliases, gateway_aliases):
                    targets = node.targets
                else:
                    targets = []
            elif isinstance(node, ast.AnnAssign):
                targets = (
                    [node.target]
                    if node.value is not None
                    and _capability_source(node.value, aliases, gateway_aliases)
                    else []
                )
            elif isinstance(node, ast.NamedExpr):
                targets = (
                    [node.target]
                    if _capability_source(node.value, aliases, gateway_aliases)
                    else []
                )
            else:
                targets = []
            for target in targets:
                names = _assigned_names(target)
                if not names.issubset(aliases):
                    aliases.update(names)
                    changed = True
    return aliases


def _capability_receiver(
    node: ast.AST,
    aliases: set[str],
    gateway_aliases: set[str],
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in aliases
    if isinstance(node, ast.Attribute):
        return (
            node.attr.casefold() == "capabilities"
            and _gateway_receiver(node.value, gateway_aliases)
        ) or _capability_receiver(
            node.value, aliases, gateway_aliases
        )
    if isinstance(node, ast.Subscript):
        return (
            _literal_string(node.slice) == "capabilities"
            or _capability_receiver(node.value, aliases, gateway_aliases)
        )
    method = _literal_getattr_method(node, {"capabilities"})
    if method is not None and isinstance(node, ast.Call):
        return _gateway_receiver(node.args[0], gateway_aliases)
    return False


def _capability_method_source(
    node: ast.AST,
    capability_aliases: set[str],
    method_aliases: set[str],
    gateway_aliases: set[str],
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in method_aliases
    if isinstance(node, ast.Attribute):
        return node.attr in {"get", "__getitem__"} and _capability_receiver(
            node.value, capability_aliases, gateway_aliases
        )
    method = _literal_getattr_method(node, {"get", "__getitem__"})
    if method is not None and isinstance(node, ast.Call):
        return _capability_receiver(node.args[0], capability_aliases, gateway_aliases)
    return False


def _raw_capability_read(
    node: ast.AST,
    aliases: set[str],
    method_aliases: set[str],
    gateway_aliases: set[str],
) -> bool:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr not in {"get", "__getitem__"} or not node.args:
            return False
        return _capability_receiver(node.func.value, aliases, gateway_aliases) and (
            _literal_string(node.args[0]) in _CAPABILITY_KEYS
        )
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in method_aliases and bool(node.args) and (
            _literal_string(node.args[0]) in _CAPABILITY_KEYS
        )
    if isinstance(node, ast.Subscript):
        return _capability_receiver(node.value, aliases, gateway_aliases) and (
            _literal_string(node.slice) in _CAPABILITY_KEYS
        )
    if isinstance(node, ast.Call):
        method = _literal_getattr_method(node.func, {"get", "__getitem__"})
        if method is not None and isinstance(node.func, ast.Call):
            return (
                bool(node.args)
                and _capability_receiver(node.func.args[0], aliases, gateway_aliases)
                and _literal_string(node.args[0]) in _CAPABILITY_KEYS
            )
    return False


def _model_call_dict(node: ast.Dict) -> bool:
    values = {
        _literal_string(key): _literal_string(value)
        for key, value in zip(node.keys, node.values, strict=True)
        if key is not None
    }
    return values.get("type") == "model_call" or values.get("metric_type") == "model_call"


def _context_receiver(node: ast.AST, aliases: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in aliases
    if isinstance(node, ast.Attribute):
        return node.attr.casefold() in _CONTEXT_ROOT_NAMES
    return False


def _context_source(
    node: ast.AST,
    base_aliases: set[str],
    aliases: set[str],
) -> bool:
    return _context_receiver(node, base_aliases | aliases)


def _metric_method_source(
    node: ast.AST,
    context_aliases: set[str],
    method_aliases: set[str],
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in method_aliases
    if isinstance(node, ast.Attribute):
        return node.attr == "record_metric" and _context_receiver(
            node.value, context_aliases
        )
    method = _literal_getattr_method(node, {"record_metric"})
    if method is not None and isinstance(node, ast.Call):
        return _context_receiver(node.args[0], context_aliases)
    return False


def _model_call_publication(
    node: ast.Call,
    context_aliases: set[str],
    method_aliases: set[str],
) -> bool:
    if not isinstance(node.func, (ast.Name, ast.Attribute)):
        method = _literal_getattr_method(node.func, {"record_metric"})
        return (
            method is not None
            and isinstance(node.func, ast.Call)
            and _context_receiver(node.func.args[0], context_aliases)
            and bool(node.args)
            and _literal_string(node.args[0]) == "model_call"
        )
    name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
    if name == "record_metric":
        return (
            isinstance(node.func, ast.Attribute)
            and _context_receiver(node.func.value, context_aliases)
            and bool(node.args)
            and _literal_string(node.args[0]) == "model_call"
        )
    if isinstance(node.func, ast.Name) and node.func.id in method_aliases:
        return bool(node.args) and _literal_string(node.args[0]) == "model_call"
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in {"record_model_call", "publish_model_call"}
        and _context_receiver(node.func.value, context_aliases)
    ) or (isinstance(node.func, ast.Name) and name in {"record_model_call", "publish_model_call"})


def _check_tree(tree: ast.AST, relative: str) -> list[str]:
    findings: list[str] = []
    gateway_aliases = _collect_gateway_aliases(tree)
    context_aliases = set(_CONTEXT_ROOT_NAMES)
    context_aliases.update(
        _annotated_names(tree, "agent.runtime.context", _CONTEXT_TYPE_NAMES)
    )
    context_aliases.update(
        _collect_bound_method_aliases(tree, _context_source, context_aliases)
    )
    capability_aliases = _collect_capability_aliases(tree, gateway_aliases)
    gateway_method_aliases = _collect_bound_method_aliases(
        tree, _gateway_method_source, gateway_aliases
    )
    capability_method_aliases = _collect_bound_method_aliases(
        tree,
        lambda node, base, methods: _capability_method_source(
            node, base, methods, gateway_aliases
        ),
        capability_aliases,
    )
    metric_method_aliases = _collect_bound_method_aliases(
        tree, _metric_method_source, context_aliases
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _gateway_call(
            node, gateway_aliases, gateway_method_aliases
        ):
            allowed = relative in GATEWAY_OWNER_FILES or relative.startswith(GATEWAY_OWNER_PREFIXES)
            if not allowed:
                findings.append(
                    f"S1: {relative}:{getattr(node, 'lineno', 0)} direct gateway complete/stream outside owner"
                )
        if _profile_selection(node) and relative not in PROFILE_SELECTION_BOUNDARY_FILES:
            findings.append(
                f"S2: {relative}:{getattr(node, 'lineno', 0)} direct profile selection outside canonical input"
            )
        if (
            _raw_capability_read(
                node,
                capability_aliases,
                capability_method_aliases,
                gateway_aliases,
            )
            and relative not in CAPABILITY_INPUT_FILES
        ):
            findings.append(
                f"S4: {relative}:{getattr(node, 'lineno', 0)} raw capability reinterpretation outside input edge"
            )
        if isinstance(node, ast.Dict) and _model_call_dict(node) and relative not in METRIC_OWNER_FILES:
            findings.append(
                f"S3: {relative}:{getattr(node, 'lineno', 0)} parallel model_call dict construction"
            )
        if (
            isinstance(node, ast.Call)
            and _model_call_publication(
                node, context_aliases, metric_method_aliases
            )
            and relative not in METRIC_OWNER_FILES
        ):
            findings.append(
                f"S3: {relative}:{getattr(node, 'lineno', 0)} parallel model_call publication"
            )
        if relative == "agent/code/workflow_proposal.py" and isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr in {
                "reserve_model_call",
                "consume_model_call",
                "finalize_model_call",
                "record_metric",
            }:
                findings.append(
                    f"S5: {relative}:{node.lineno} coding workflow owns model-call lifecycle"
                )
    return findings


def check_source(source: str, relative: str) -> list[str]:
    """Check a source snippet using the same semantic gates as repository files."""

    return _check_tree(ast.parse(source, filename=relative), relative)


def _check_file(path: Path) -> list[str]:
    relative = _relative(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    return _check_tree(tree, relative)


def run_checks() -> list[str]:
    findings: list[str] = []
    for path in _files():
        findings.extend(_check_file(path))
    return sorted(set(findings))


def main() -> int:
    findings = run_checks()
    if findings:
        print("Wave 1 architecture gates failed:")
        print("\n".join(f"- {finding}" for finding in findings))
        return 1
    print("Wave 1 architecture gates passed (S1-S5).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
