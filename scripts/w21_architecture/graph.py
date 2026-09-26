"""Deterministic architecture graph construction and signatures."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable

from .imports import STATIC_KINDS, aggregate_edges, collect_import_edges
from .source import RepositorySource


def package_family(module: str) -> str:
    """Return the first package family below the ``agent`` namespace."""

    return module if module == "agent" else ".".join(module.split(".")[:2])


def _package_edges(edges: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for edge in edges:
        source = package_family(str(edge["source_module"]))
        destination = package_family(str(edge["destination_module"]))
        if source == destination:
            continue
        key = (source, destination)
        record = grouped.setdefault(
            key,
            {
                "source_package": source,
                "destination_package": destination,
                "module_edge_count": 0,
                "import_statement_count": 0,
                "edge_kinds": set(),
            },
        )
        record["module_edge_count"] = int(record["module_edge_count"]) + 1
        evidence = edge.get("evidence", ())
        record["import_statement_count"] = int(record["import_statement_count"]) + len(evidence)  # type: ignore[arg-type]
        edge_kinds = record["edge_kinds"]
        assert isinstance(edge_kinds, set)
        edge_kinds.update(edge.get("edge_kinds", ()))  # type: ignore[arg-type]

    result: list[dict[str, object]] = []
    for key in sorted(grouped):
        record = grouped[key]
        edge_kinds = record["edge_kinds"]
        assert isinstance(edge_kinds, set)
        result.append({**record, "edge_kinds": sorted(edge_kinds)})
    return result


def _semantic_hash(edges: list[dict[str, object]]) -> str:
    semantic = [
        {
            "source_module": record["source_module"],
            "destination_module": record["destination_module"],
            "edge_kinds": record["edge_kinds"],
        }
        for record in edges
    ]
    payload = json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass
class _SccState:
    adjacency: dict[str, set[str]]
    index: int = 0
    stack: list[str] | None = None
    on_stack: set[str] | None = None
    indices: dict[str, int] | None = None
    lowlink: dict[str, int] | None = None
    count: int = 0

    def __post_init__(self) -> None:
        self.stack = []
        self.on_stack = set()
        self.indices = {}
        self.lowlink = {}


def _visit_scc(node: str, state: _SccState) -> None:
    assert state.stack is not None
    assert state.on_stack is not None
    assert state.indices is not None
    assert state.lowlink is not None
    state.indices[node] = state.index
    state.lowlink[node] = state.index
    state.index += 1
    state.stack.append(node)
    state.on_stack.add(node)
    for destination in sorted(state.adjacency[node]):
        if destination not in state.indices:
            _visit_scc(destination, state)
            state.lowlink[node] = min(state.lowlink[node], state.lowlink[destination])
        elif destination in state.on_stack:
            state.lowlink[node] = min(state.lowlink[node], state.indices[destination])
    if state.lowlink[node] != state.indices[node]:
        return
    component: list[str] = []
    while True:
        value = state.stack.pop()
        state.on_stack.remove(value)
        component.append(value)
        if value == node:
            break
    if len(component) > 1 or node in state.adjacency[node]:
        state.count += 1


def _scc_count(nodes: Iterable[str], edges: list[dict[str, object]]) -> int:
    adjacency = {node: set() for node in nodes}
    for edge in edges:
        source = str(edge["source_module"])
        destination = str(edge["destination_module"])
        if source in adjacency and destination in adjacency:
            adjacency[source].add(destination)
    state = _SccState(adjacency)
    assert state.indices is not None
    for node in sorted(adjacency):
        if node not in state.indices:
            _visit_scc(node, state)
    return state.count


def _module_identity(records: Iterable[dict[str, str]]) -> str:
    payload = json.dumps(list(records), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ArchitectureGraph:
    modules: tuple[dict[str, str], ...]
    static_edges: tuple[dict[str, object], ...]
    literal_dynamic_edges: tuple[dict[str, object], ...]
    declarative_edges: tuple[dict[str, object], ...]
    architecture_union_edges: tuple[dict[str, object], ...]
    static_package_edges: tuple[dict[str, object], ...]
    architecture_union_package_edges: tuple[dict[str, object], ...]

    def signatures(self) -> dict[str, object]:
        module_names = tuple(item["module"] for item in self.modules)
        return {
            "production_module_count": len(self.modules),
            "module_identity_sha256": _module_identity(self.modules),
            "static_module_edge_count": len(self.static_edges),
            "static_module_edge_semantic_sha256": _semantic_hash(list(self.static_edges)),
            "architecture_union_edge_count": len(self.architecture_union_edges),
            "architecture_union_edge_semantic_sha256": _semantic_hash(list(self.architecture_union_edges)),
            "static_package_edge_count": len(self.static_package_edges),
            "architecture_union_package_edge_count": len(self.architecture_union_package_edges),
            "static_nontrivial_scc_count": _scc_count(module_names, list(self.static_edges)),
            "architecture_union_nontrivial_scc_count": _scc_count(module_names, list(self.architecture_union_edges)),
        }


def build_graph(source: RepositorySource) -> ArchitectureGraph:
    """Build static and architecture-union views from the explicit root."""

    edges = collect_import_edges(source)
    static = aggregate_edges(edges, set(STATIC_KINDS))
    dynamic = aggregate_edges(edges, {"literal_dynamic_import"})
    declarative = aggregate_edges(edges, {"declarative_runtime_import"})
    union = aggregate_edges(edges)
    modules = tuple(
        {"module": module, "path": source.relative(path)}
        for module, path in sorted(source.module_paths().items())
    )
    return ArchitectureGraph(
        modules=modules,
        static_edges=tuple(static),
        literal_dynamic_edges=tuple(dynamic),
        declarative_edges=tuple(declarative),
        architecture_union_edges=tuple(union),
        static_package_edges=tuple(_package_edges(static)),
        architecture_union_package_edges=tuple(_package_edges(union)),
    )
