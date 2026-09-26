"""Normalization and exact-match mechanics for the frozen bridge registry."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Mapping

from .source import RepositorySource, qualified_name

LEGACY_PATH_SYMBOLS = frozenset(
    {
        "RUNTIME_DIR",
        "LOG_FILE",
        "CHECKPOINT_FILE",
        "MEMORY_FILE",
        "MEMORY_DB_FILE",
        "REPORTS_DIR",
        "METRICS_FILE",
        "TASK_TRACKER_JSON",
    }
)


def _is_python_surface(value: str) -> bool:
    return value.startswith("agent.") and " " not in value and "::" not in value


def _is_symbol(part: str) -> bool:
    return bool(part) and (part[0].isupper() or part.isupper())


def _surface(value: str) -> tuple[str, str | None]:
    if not _is_python_surface(value):
        return value, None
    parts = value.split(".")
    if len(parts) > 2 and _is_symbol(parts[-1]):
        return ".".join(parts[:-1]), parts[-1]
    return value, None


def _package_match(module: str, package: str) -> bool:
    return module == package or module.startswith(package + ".")


@dataclass(frozen=True)
class ModuleBridge:
    bridge_id: str
    source_module: str
    target_module: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class SymbolBridge:
    bridge_id: str
    source_module: str
    source_symbol: str
    target_module: str
    target_symbol: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class AdapterEdge:
    adapter_id: str
    source_module: str
    destination_package: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class CompatibilityRegistry:
    module_bridges: tuple[ModuleBridge, ...]
    symbol_bridges: tuple[SymbolBridge, ...]
    adapters: tuple[AdapterEdge, ...]
    forbidden_expansions: tuple[str, ...]
    document: Mapping[str, Any]

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "CompatibilityRegistry":
        modules: list[ModuleBridge] = []
        symbols: list[SymbolBridge] = []
        for raw in document.get("bridges", []):
            if not isinstance(raw, Mapping):
                continue
            bridge_id = str(raw.get("bridge_id", ""))
            source_module, source_symbol = _surface(str(raw.get("surface", "")))
            target_module, target_symbol = _surface(str(raw.get("target_owner", "")))
            if not bridge_id or source_symbol is None or target_symbol is None:
                if _is_python_surface(str(raw.get("surface", ""))) and _is_python_surface(str(raw.get("target_owner", ""))):
                    modules.append(ModuleBridge(bridge_id, source_module, target_module, raw))
            else:
                symbols.append(SymbolBridge(bridge_id, source_module, source_symbol, target_module, target_symbol, raw))

        adapters: list[AdapterEdge] = []
        for raw in document.get("adapter_edges", []):
            if not isinstance(raw, Mapping):
                continue
            source = raw.get("source_module") or raw.get("source_surface")
            destination = raw.get("destination_package")
            if not isinstance(source, str) or not isinstance(destination, str):
                continue
            source_module, _ = _surface(source)
            adapters.append(AdapterEdge(str(raw.get("adapter_id", "")), source_module, destination, raw))
        return cls(
            tuple(modules),
            tuple(symbols),
            tuple(adapters),
            tuple(str(item) for item in document.get("forbidden_compatibility_expansion", [])),
            document,
        )

    def exact_module_bridge(self, source: str, destination: str) -> ModuleBridge | None:
        return next((item for item in self.module_bridges if item.source_module == source and item.target_module == destination), None)

    def exact_adapter(self, source: str, destination: str) -> AdapterEdge | None:
        return next(
            (
                item
                for item in self.adapters
                if item.source_module == source and _package_match(destination, item.destination_package)
            ),
            None,
        )

    def facade_for(self, source: str) -> tuple[ModuleBridge, ...]:
        return tuple(item for item in self.module_bridges if item.source_module == source)


def normalize_registry(document: Mapping[str, Any]) -> CompatibilityRegistry:
    """Parse the registry once for all policy and compatibility consumers."""

    return CompatibilityRegistry.from_document(document)


def resolve_relative_module(
    source_module: str,
    level: int,
    module: str | None,
    *,
    package_init: bool = False,
) -> str:
    """Resolve a relative import from a production module name."""

    parts = source_module.split(".")
    package = parts if package_init else parts[:-1]
    upward = level - 1
    if upward:
        package = package[:-upward] if upward <= len(package) else []
    base = ".".join(package)
    return f"{base}.{module}" if base and module else base or (module or "")


def _legacy_module_for_from(source_module: str, node: ast.ImportFrom, *, package_init: bool) -> str:
    if node.level:
        base = resolve_relative_module(source_module, node.level, node.module, package_init=package_init)
        return base
    return node.module or ""


def _legacy_from_imports(
    node: ast.ImportFrom,
    source_module: str,
    *,
    package_init: bool,
) -> tuple[set[tuple[str, str]], dict[str, str]]:
    consumers: set[tuple[str, str]] = set()
    aliases: dict[str, str] = {}
    imported_module = _legacy_module_for_from(source_module, node, package_init=package_init)
    if imported_module == "agent.runtime.paths":
        if any(alias.name == "*" for alias in node.names):
            # A wildcard import is a closed-world consumption of every
            # legacy export.  Recording the full surface makes an expansion
            # visible even when the module already had a baseline edge to
            # agent.runtime.paths.
            consumers.update((source_module, symbol) for symbol in LEGACY_PATH_SYMBOLS)
        consumers.update(
            (source_module, alias.name)
            for alias in node.names
            if alias.name in LEGACY_PATH_SYMBOLS
        )
    elif imported_module == "agent.runtime":
        aliases.update(
            (alias.asname or alias.name, "agent.runtime.paths")
            for alias in node.names
            if alias.name == "paths"
        )
    return consumers, aliases


def _legacy_import_bindings(
    tree: ast.AST,
    source_module: str,
    *,
    package_init: bool,
) -> tuple[set[tuple[str, str]], dict[str, str]]:
    consumers: set[tuple[str, str]] = set()
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "agent.runtime.paths":
                    bound = alias.asname or alias.name.split(".")[0]
                    aliases[bound] = alias.name
        elif isinstance(node, ast.ImportFrom):
            imported_consumers, imported_aliases = _legacy_from_imports(
                node,
                source_module,
                package_init=package_init,
            )
            consumers.update(imported_consumers)
            aliases.update(imported_aliases)
    return consumers, aliases


def _legacy_attribute_consumers(
    tree: ast.AST,
    source_module: str,
    module_aliases: Mapping[str, str],
) -> set[tuple[str, str]]:
    consumers: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr not in LEGACY_PATH_SYMBOLS:
            continue
        dotted = ".".join(qualified_name(node.value))
        module = module_aliases.get(dotted)
        if module == "agent.runtime.paths" or dotted == "agent.runtime.paths":
            consumers.add((source_module, node.attr))
    return consumers


def legacy_consumers_from_tree(
    tree: ast.AST,
    source_module: str,
    *,
    package_init: bool = False,
) -> frozenset[tuple[str, str]]:
    """Extract legacy path consumers from one already parsed module."""

    consumers, module_aliases = _legacy_import_bindings(
        tree,
        source_module,
        package_init=package_init,
    )
    consumers.update(_legacy_attribute_consumers(tree, source_module, module_aliases))
    return frozenset(consumers)


def legacy_consumers(source: RepositorySource) -> frozenset[tuple[str, str]]:
    """Extract direct, relative, and module-alias legacy path consumers."""

    consumers: set[tuple[str, str]] = set()
    module_paths = source.module_paths()
    for source_module, path in sorted(module_paths.items()):
        if source_module == "agent.runtime.paths":
            continue
        tree = source.tree_for_path(path)
        if tree is None:
            continue
        consumers.update(
            legacy_consumers_from_tree(
                tree,
                source_module,
                package_init=path.name == "__init__.py",
            )
        )
    return frozenset(consumers)
