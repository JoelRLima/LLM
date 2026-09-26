"""AST import-edge extraction, including the bounded runtime edges in W21."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .source import RepositorySource, is_type_checking_test, literal_value, qualified_name

STATIC_KINDS = frozenset({"normal_import", "local_import", "type_checking", "reexport"})


@dataclass(frozen=True)
class ImportEdge:
    source_module: str
    destination_module: str
    edge_kind: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class _Context:
    function_depth: int = 0
    type_checking_depth: int = 0
    package_init: bool = False


def imported_module_names(tree: ast.AST) -> frozenset[str]:
    """Return the raw module names present in import statements.

    This is intentionally a bounded mechanics helper.  Historical checkers
    retain their own policy and violation text while sharing this extraction.
    Relative imports retain their leading dots so callers can distinguish them
    from absolute imports.
    """

    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            result.add(prefix + (node.module or ""))
    return frozenset(result)


def _relative_base(source_module: str, level: int, module: str | None, *, package_init: bool) -> str:
    if not level:
        return module or ""
    parts = source_module.split(".")
    if not package_init:
        parts = parts[:-1]
    upward = level - 1
    if upward:
        parts = parts[:-upward] if upward <= len(parts) else []
    base = ".".join(parts)
    return f"{base}.{module}" if base and module else base or (module or "")


def _destination(candidate: str | None, modules: set[str]) -> str | None:
    if not candidate:
        return None
    if candidate in modules:
        return candidate
    parts = candidate.split(".")
    for index in range(len(parts) - 1, 0, -1):
        value = ".".join(parts[:index])
        if value in modules:
            return value
    return None


def _import_from_destinations(
    node: ast.ImportFrom,
    source_module: str,
    modules: set[str],
    *,
    package_init: bool,
) -> tuple[str, ...]:
    base = _relative_base(source_module, node.level, node.module, package_init=package_init)
    destinations: list[str] = []
    for alias in node.names:
        candidate = base if alias.name == "*" else f"{base}.{alias.name}" if base else alias.name
        destination = _destination(candidate, modules) or _destination(base, modules)
        if destination and destination not in destinations:
            destinations.append(destination)
    return tuple(destinations)


def _static_destinations(
    node: ast.Import | ast.ImportFrom,
    source_module: str,
    modules: set[str],
    *,
    package_init: bool,
) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        candidates = tuple(alias.name for alias in node.names)
        destinations: list[str] = []
        for candidate in candidates:
            destination = _destination(candidate, modules)
            if destination and destination not in destinations:
                destinations.append(destination)
        return tuple(destinations)
    return _import_from_destinations(node, source_module, modules, package_init=package_init)


def _edge_kind(context: _Context) -> str:
    if context.type_checking_depth:
        return "type_checking"
    if context.function_depth:
        return "local_import"
    if context.package_init:
        return "reexport"
    return "normal_import"


def _edge_variants(kind: str) -> tuple[str, ...]:
    if kind == "normal_import":
        return ("normal_import",)
    if kind == "type_checking":
        return ("normal_import", "type_checking")
    if kind == "reexport":
        return ("normal_import", "reexport")
    return (kind,)


def _record_static(
    edges: list[ImportEdge],
    module: str,
    kind: str,
    evidence: tuple[str, ...],
    destinations: tuple[str, ...],
) -> None:
    for destination in destinations:
        for edge_kind in _edge_variants(kind):
            edges.append(ImportEdge(module, destination, edge_kind, evidence))


def _collect_static(source: RepositorySource, module: str, tree: ast.Module) -> list[ImportEdge]:
    modules = set(source.module_paths())
    path = source.module_paths()[module]
    root_context = _Context(package_init=path.name == "__init__.py")
    edges: list[ImportEdge] = []

    def visit(node: ast.AST, context: _Context) -> None:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            evidence = (f"{source.relative(path)}:{node.lineno}",)
            destinations = _static_destinations(
                node,
                module,
                modules,
                package_init=root_context.package_init,
            )
            _record_static(edges, module, _edge_kind(context), evidence, destinations)

        if isinstance(node, ast.If) and is_type_checking_test(node.test):
            # Only the true branch is guarded.  The else branch is ordinary
            # code; propagating TYPE_CHECKING into it was a rejected-candidate
            # bug that changed the frozen graph.
            for child in ast.iter_child_nodes(node.test):
                visit(child, context)
            guarded = _Context(
                context.function_depth,
                context.type_checking_depth + 1,
                context.package_init,
            )
            for child in node.body:
                visit(child, guarded)
            for child in node.orelse:
                visit(child, context)
            return

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            child_context = _Context(
                context.function_depth + 1,
                context.type_checking_depth,
                context.package_init,
            )
        else:
            child_context = context
        for child in ast.iter_child_nodes(node):
            visit(child, child_context)

    visit(tree, root_context)
    return edges


def _collect_literal_dynamic(source: RepositorySource, module: str, tree: ast.Module) -> list[ImportEdge]:
    path = source.module_paths()[module]
    modules = set(source.module_paths())
    edges: list[ImportEdge] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or qualified_name(node.func)[-2:] != ("importlib", "import_module"):
            continue
        value = literal_value(node.args[0]) if node.args else None
        destination = _destination(value if isinstance(value, str) else None, modules)
        if destination:
            edges.append(
                ImportEdge(
                    module,
                    destination,
                    "literal_dynamic_import",
                    (f"{source.relative(path)}:{node.lineno}",),
                )
            )
    return edges


def _keyword_literal(call: ast.Call, name: str) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            value = literal_value(keyword.value)
            return value if isinstance(value, str) else None
    return None


def _positional_literal(call: ast.Call, index: int | None) -> str | None:
    if index is None or len(call.args) <= index:
        return None
    value = literal_value(call.args[index])
    return value if isinstance(value, str) else None


def _declarative_destination(
    node: ast.Call,
    modules: set[str],
) -> tuple[str, str] | None:
    name = qualified_name(node.func)[-1:]
    if name == ("SkillSpec",):
        value = _keyword_literal(node, "module") or _positional_literal(node, 0)
        destination = _destination(value, modules) if value else None
        return ("agent.skills.registry", destination) if destination else None

    handler_positions = {"_binding": 8, "c": 3, "q": 3, "a": 3, "CliActionBinding": 0}
    call_name = name[0] if name else ""
    if call_name not in handler_positions:
        return None
    value = _keyword_literal(node, "handler_owner") or _positional_literal(node, handler_positions[call_name])
    if not value:
        return None
    owner = value.rsplit(".", 1)[0] if "." in value else value
    destination = _destination(owner, modules)
    return ("agent.interfaces.cli.action_registry", destination) if destination else None


def _collect_declarative(source: RepositorySource, module: str, tree: ast.Module) -> list[ImportEdge]:
    path = source.module_paths()[module]
    modules = set(source.module_paths())
    edges: list[ImportEdge] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        result = _declarative_destination(node, modules)
        if result is None:
            continue
        source_module, destination = result
        edges.append(
            ImportEdge(
                source_module,
                destination,
                "declarative_runtime_import",
                (f"{source.relative(path)}:{node.lineno}",),
            )
        )
    return edges


def collect_import_edges(source: RepositorySource) -> tuple[ImportEdge, ...]:
    """Collect static, literal dynamic, and closed declarative edges."""

    result: list[ImportEdge] = []
    for module, path in sorted(source.module_paths().items()):
        tree = source.tree_for_path(path)
        if tree is None:
            continue
        result.extend(_collect_static(source, module, tree))
        result.extend(_collect_literal_dynamic(source, module, tree))
        result.extend(_collect_declarative(source, module, tree))
    return tuple(result)


def aggregate_edges(
    edges: tuple[ImportEdge, ...],
    kinds: set[str] | None = None,
) -> list[dict[str, object]]:
    """Aggregate occurrences into deterministic source/destination records."""

    selected = [edge for edge in edges if kinds is None or edge.edge_kind in kinds]
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for edge in selected:
        key = (edge.source_module, edge.destination_module)
        record = grouped.setdefault(
            key,
            {"source_module": key[0], "destination_module": key[1], "edge_kinds": set(), "evidence": []},
        )
        edge_kinds = record["edge_kinds"]
        evidence = record["evidence"]
        assert isinstance(edge_kinds, set) and isinstance(evidence, list)
        edge_kinds.add(edge.edge_kind)
        evidence.extend(edge.evidence)

    result: list[dict[str, object]] = []
    for key in sorted(grouped):
        record = grouped[key]
        edge_kinds = record["edge_kinds"]
        evidence = record["evidence"]
        assert isinstance(edge_kinds, set) and isinstance(evidence, list)
        result.append(
            {
                "source_module": key[0],
                "destination_module": key[1],
                "edge_kinds": sorted(edge_kinds),
                "evidence": sorted(set(evidence)),
            }
        )
    return result
