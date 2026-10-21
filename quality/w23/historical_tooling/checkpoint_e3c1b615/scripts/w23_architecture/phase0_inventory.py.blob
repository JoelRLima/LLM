"""Generate deterministic Wave 23 Phase-0 graph and inventory evidence.

This is analysis tooling, not a runtime package or a policy checker. It reuses
the repository AST graph collector and overlays the finite symbol mapping in
``application.agent_boundary`` so its variable-driven imports remain visible.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
CANONICALIZATION_VERSION = "json-sort-keys-compact-utf8-lf-v1"
GENERATOR_ID = "scripts/w23_architecture/phase0_inventory.py@1"
AUTHORITY_COMMIT = "e61faff9d04ef7718de7764d61b29c7059d77b96"
AUTHORITY_TREE = "5d858db4b3e6b18e60ac1836c0ed878833dd7549"
FORBIDDEN_OWNER_EDGES = (
    ("Platform", "Agent"), ("Platform", "Interfaces"),
    ("Agent", "Application"), ("Agent", "Interfaces"),
    ("Application", "Interfaces"), ("Interfaces", "Agent"),
    ("Interfaces", "Platform"),
)
PREFIX_FAMILIES = {
    "planning": ("task_semantics_", "plan_", "target_grounding_", "replan_", "result_binding", "execution_", "observation_", "validation_repair_", "parallel_", "hierarchical_"),
    "evaluation": ("campaign_", "analysis_", "attribution_", "scripted_", "practical_", "receipt_", "execution_", "feedback_"),
    "runtime": ("config_", "storage_", "task_policy_", "model_call_", "outcome_", "event_", "lock_"),
    "tools": ("invocation_", "extension_", "workspace_extensions_", "stdio_"),
    "observability": ("trace_", "audit_projection_"),
    "agent_root": ("application_", "checkpoint_", "state_", "final_response", "workspace"),
}


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(root), *args), check=True, capture_output=True,
        text=True, encoding="utf-8",
    )
    return result.stdout.strip()


def nearest_module(candidate: str, modules: set[str]) -> str | None:
    parts = candidate.split(".") if candidate else []
    for index in range(len(parts), 0, -1):
        name = ".".join(parts[:index])
        if name in modules:
            return name
    return None


def strongly_connected(nodes: set[str], edges: list[dict[str, Any]]) -> list[list[str]]:
    adjacency: dict[str, set[str]] = {node: set() for node in nodes}
    for edge in edges:
        source, target = str(edge["source_module"]), str(edge["destination_module"])
        if source in adjacency and target in adjacency:
            adjacency[source].add(target)
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    result: list[list[str]] = []

    state: dict[str, Any] = {"index": index, "stack": stack, "on_stack": on_stack, "indices": indices,
             "lowlinks": lowlinks, "result": result, "adjacency": adjacency}
    for node in sorted(nodes):
        if node not in state["indices"]:
            _visit_component(node, state)
    return sorted(result, key=lambda members: (len(members), members))


def _visit_component(node: str, state: dict[str, Any]) -> None:
    indices = state["indices"]
    lowlinks = state["lowlinks"]
    indices[node] = lowlinks[node] = state["index"]
    state["index"] += 1
    stack = state["stack"]
    on_stack = state["on_stack"]
    stack.append(node)
    on_stack.add(node)
    for target in sorted(state["adjacency"][node]):
        if target not in indices:
            _visit_component(target, state)
            lowlinks[node] = min(lowlinks[node], lowlinks[target])
        elif target in on_stack:
            lowlinks[node] = min(lowlinks[node], indices[target])
    if lowlinks[node] != indices[node]:
        return
    component: list[str] = []
    while True:
        member = stack.pop()
        on_stack.remove(member)
        component.append(member)
        if member == node:
            break
    if len(component) > 1 or node in state["adjacency"][node]:
        state["result"].append(sorted(component))


def domain_family(module: str, owner: str) -> str:
    parts = module.split(".")
    if owner == "Agent":
        return f"Agent.{parts[2] if len(parts) > 2 else 'root'}"
    family = parts[1] if len(parts) > 1 else "root"
    if owner == "Interfaces" and len(parts) > 2:
        family = f"{family}.{parts[2]}"
    return f"{owner}.{family}"


def test_consumers(root: Path, modules: set[str]) -> dict[str, list[str]]:
    consumers: dict[str, set[str]] = defaultdict(set)
    tests = root / "tests"
    if not tests.exists():
        return {}
    for path in sorted(tests.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            for candidate in _test_import_candidates(node):
                module = nearest_module(candidate, modules)
                if module:
                    consumers[module].add(relative)
    return {module: sorted(paths) for module, paths in sorted(consumers.items())}


def _test_import_candidates(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.level == 0:
        base = node.module or ""
        return [base, *(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "import_module" and node.args:
        try:
            value = ast.literal_eval(node.args[0])
        except (ValueError, TypeError):
            return []
        return [value] if isinstance(value, str) else []
    return []


def boundary_inventory(root: Path, modules: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    boundary = "llm_agent.application.agent_boundary"
    exports = _boundary_exports(root)
    consumers: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"symbols": set(), "module_import": set()})
    paths = sorted((root / "src").rglob("*.py")) + sorted((root / "tests").rglob("*.py"))
    for path in paths:
        relative = path.relative_to(root).as_posix()
        try:
            parsed = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, SyntaxError, UnicodeError):
            continue
        aliases, component_aliases, imported_symbols, found = _boundary_file_symbols(parsed, boundary, exports)
        if not found and not (aliases or component_aliases):
            continue
        consumers[relative]["symbols"].update(found)
        if aliases or component_aliases or found:
            consumers[relative]["module_import"].add(boundary)

    export_rows, registry_edges = _boundary_export_rows(boundary, exports, consumers, modules)
    consumer_rows = [
        {"path": path, "symbols": sorted(row["symbols"]), "module_import": bool(row["module_import"])}
        for path, row in sorted(consumers.items())
    ]
    return export_rows, consumer_rows, registry_edges


def _boundary_exports(root: Path) -> dict[str, tuple[str, str]]:
    relative = "src/llm_agent/application/agent_boundary.py"
    path = root / relative
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    mapping = next((node.value for node in tree.body if isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name) and node.target.id == "_EXPORTS"), None)
    if mapping is None:
        raise ValueError("agent_boundary._EXPORTS literal mapping is missing")
    exports = ast.literal_eval(mapping)
    if not isinstance(exports, dict):
        raise ValueError("agent_boundary._EXPORTS is not a literal mapping")
    return exports


def _boundary_file_symbols(tree: ast.Module, boundary: str, exports: dict[str, Any]) -> tuple[set[str], set[str], dict[str, str], set[str]]:
    aliases: set[str] = set()
    component_aliases: set[str] = set()
    imported_symbols: dict[str, str] = {}
    found: set[str] = set()
    for node in ast.walk(tree):
        _collect_boundary_import(node, boundary, exports, aliases, component_aliases, imported_symbols, found)
        _collect_boundary_string(node, boundary, exports, found)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in exports:
            _collect_boundary_attribute(node, boundary, aliases, component_aliases, imported_symbols, found)
    return aliases, component_aliases, imported_symbols, found


def _collect_boundary_import(node: ast.AST, boundary: str, exports: dict[str, Any], aliases: set[str], component_aliases: set[str], imported: dict[str, str], found: set[str]) -> None:
    if isinstance(node, ast.ImportFrom) and node.module in {boundary, "llm_agent.application"}:
        for alias in node.names:
            if node.module == boundary and alias.name in exports:
                found.add(alias.name)
                imported[alias.asname or alias.name] = alias.name
            elif node.module == boundary and alias.name == "*":
                found.update(exports)
            elif node.module == "llm_agent.application" and alias.name == "agent_boundary":
                component_aliases.add(alias.asname or alias.name)
    elif isinstance(node, ast.Import):
        aliases.update(alias.asname or "llm_agent" for alias in node.names if alias.name == boundary)
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "import_module" and node.args:
        try:
            imported_module = ast.literal_eval(node.args[0])
        except (ValueError, TypeError):
            return
        if imported_module == boundary:
            found.update(exports)


def _collect_boundary_string(node: ast.AST, boundary: str, exports: dict[str, Any], found: set[str]) -> None:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return
    prefix = boundary + "."
    if node.value.startswith(prefix):
        symbol = node.value[len(prefix):].split(".", 1)[0]
        if symbol in exports:
            found.add(symbol)


def _collect_boundary_attribute(node: ast.Attribute, boundary: str, aliases: set[str], component_aliases: set[str], imported: dict[str, str], found: set[str]) -> None:
    if isinstance(node.value, ast.Name) and node.value.id in component_aliases | aliases:
        found.add(node.attr)
    if isinstance(node.value, ast.Name) and node.value.id in imported:
        found.add(imported[node.value.id])
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        parts.reverse()
        if ".".join(parts[:-1]) == boundary or (parts[0] in aliases and len(parts) > 1):
            found.add(node.attr)


def _boundary_export_rows(boundary: str, exports: dict[str, Any], consumers: dict[str, dict[str, set[str]]], modules: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for symbol, pair in sorted(exports.items()):
        target_module, target_symbol = pair
        production = sorted(path for path, row in consumers.items() if path.startswith("src/") and symbol in row["symbols"])
        tests = sorted(path for path, row in consumers.items() if path.startswith("tests/") and symbol in row["symbols"])
        rows.append({"symbol": symbol, "target_module": target_module, "target_symbol": target_symbol, "production_consumers": production, "test_consumers": tests})
        if target_module in modules:
            edges.append({"source_module": boundary, "destination_module": target_module, "edge_kinds": ["symbol_registry"], "evidence": [f"src/llm_agent/application/agent_boundary.py:_EXPORTS:{symbol}"]})
    return rows, edges


def build_inventory(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = root.resolve()
    head, baseline_tree, source, graph, policy, module_names, paths = _phase0_authority_inputs(root)
    owners, families, module_rows, top_package_counts, agent_package_counts, prefix_records = _phase0_module_inventory(
        root, source, graph, policy, module_names
    )
    edges, exports, broker_consumers = _phase0_edges(root, module_names, graph, owners, families)
    dynamic_calls = _phase0_dynamic_calls(root, graph, module_names)
    module_sccs = strongly_connected(module_names, edges)
    cross_domain_sccs = [component for component in module_sccs if len({families[name] for name in component}) > 1]
    family_edges = [
        {"source_module": edge["source_family"], "destination_module": edge["destination_family"]}
        for edge in edges if edge["source_family"] != "UNKNOWN" and edge["destination_family"] != "UNKNOWN"
    ]
    package_sccs = strongly_connected(set(families.values()), family_edges)
    test_map = test_consumers(root, module_names)
    forbidden = [edge for edge in edges if (edge["source_owner"], edge["destination_owner"]) in FORBIDDEN_OWNER_EDGES]
    forbidden_counts = {f"{left}->{right}": 0 for left, right in FORBIDDEN_OWNER_EDGES}
    for edge in forbidden:
        forbidden_counts[f"{edge['source_owner']}->{edge['destination_owner']}"] += 1

    raw = {
        "schema_version": SCHEMA_VERSION,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "baseline_commit": head,
        "baseline_tree": baseline_tree,
        "source_layout_profile": "final-w22",
        "modules": sorted(module_rows, key=lambda row: row["module"]),
        "edges": edges,
        "dynamic_import_calls": dynamic_calls,
        "agent_boundary_exports": exports,
        "agent_boundary_consumers": broker_consumers,
        "test_consumers_by_module": test_map,
    }
    raw_bytes = canonical_bytes(raw)
    cross_memberships = [len({families[name] for name in component}) for component in cross_domain_sccs]
    largest_families = max(cross_memberships, default=0)
    summary = _phase0_summary(
        root, head, baseline_tree, raw_bytes, module_rows, edges, owners, top_package_counts,
        agent_package_counts, exports, broker_consumers, dynamic_calls, module_sccs,
        cross_domain_sccs, largest_families, cross_memberships, package_sccs, forbidden,
        forbidden_counts, prefix_records, families,
    )
    return raw, summary


def _phase0_authority_inputs(root: Path) -> tuple[str, str, Any, Any, dict[str, Any], set[str], list[Path]]:
    head = _git(root, "rev-parse", "HEAD")
    baseline_tree = _git(root, "rev-parse", "HEAD^{tree}")
    if head != AUTHORITY_COMMIT or baseline_tree != AUTHORITY_TREE:
        raise ValueError(f"Phase-0 authority mismatch: HEAD={head} tree={baseline_tree}")
    tracked_sources = {
        path for path in _git(root, "ls-tree", "-r", "--name-only", "HEAD", "--", "src/llm_agent").splitlines()
        if path.endswith(".py")
    }
    worktree_sources = {path.relative_to(root).as_posix() for path in (root / "src/llm_agent").rglob("*.py")}
    if tracked_sources != worktree_sources:
        raise ValueError("Phase-0 production source paths differ from the authority tree")
    if _git(root, "status", "--porcelain=v1", "--", "src/llm_agent", "scripts/w21_architecture", "quality/architecture_policy.json", "quality/architecture_compatibility.json", "quality/architecture_w22_policy.json"):
        raise ValueError("Phase-0 graph inputs have tracked or untracked worktree changes")
    sys.path.insert(0, str(root))
    from scripts.w21_architecture import RepositorySource, build_graph
    source = RepositorySource(root, "final-w22")
    graph = build_graph(source)
    policy = json.loads((root / "quality/architecture_w22_policy.json").read_text(encoding="utf-8"))
    module_names = {str(row["module"]) for row in graph.modules}
    paths = sorted((root / "src/llm_agent").rglob("*.py"))
    if len(paths) != len(module_names):
        raise ValueError(f"source/graph count mismatch: {len(paths)} source files vs {len(module_names)} modules")
    return head, baseline_tree, source, graph, policy, module_names, paths


def _phase0_module_inventory(root: Path, source: Any, graph: Any, policy: dict[str, Any], module_names: set[str]) -> tuple[dict[str, str], dict[str, str], list[dict[str, Any]], dict[str, int], dict[str, int], dict[str, dict[str, list[str]]]]:
    from scripts.w21_architecture.policy import w22_owner_for_module
    owners: dict[str, str] = {}
    families: dict[str, str] = {}
    module_rows: list[dict[str, Any]] = []
    for row in graph.modules:
        module = str(row["module"])
        owner = w22_owner_for_module(module, source.layout, policy)
        if owner is None:
            raise ValueError(f"unclassified module owner: {module}")
        path = root / "src" / str(row["path"])
        owners[module] = owner
        families[module] = domain_family(module, owner)
        module_rows.append({
            "module": module,
            "path": path.relative_to(root).as_posix(),
            "owner": owner,
            "domain_family": families[module],
            "package_family": ".".join(module.split(".")[:2]),
            "loc": len(path.read_text(encoding="utf-8").splitlines()),
        })
    top_package_counts, agent_package_counts = _phase0_package_counts(module_names)
    prefix_records: dict[str, dict[str, list[str]]] = {}
    for family, prefixes in PREFIX_FAMILIES.items():
        prefix_records[family] = {}
        for prefix in prefixes:
            matches = []
            for module in module_names:
                parts = module.split(".")
                if family == "agent_root":
                    in_scope = len(parts) == 3 and parts[1] == "agent"
                else:
                    package = "llm_agent.agent." + family
                    in_scope = module.startswith(package + ".")
                leaf = parts[-1]
                if in_scope and leaf.startswith(prefix):
                    matches.append(module)
            prefix_records[family][prefix] = sorted(matches)
    return owners, families, module_rows, dict(top_package_counts), dict(agent_package_counts), prefix_records


def _phase0_package_counts(module_names: set[str]) -> tuple[dict[str, int], dict[str, int]]:
    top_package_counts: dict[str, int] = defaultdict(int)
    agent_package_counts: dict[str, int] = defaultdict(int)
    for module in module_names:
        parts = module.split(".")
        if len(parts) > 1:
            top_package_counts[parts[1]] += 1
        if len(parts) > 2 and parts[1] == "agent":
            agent_package_counts[parts[2]] += 1
    return dict(top_package_counts), dict(agent_package_counts)


def _phase0_edges(root: Path, module_names: set[str], graph: Any, owners: dict[str, str], families: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    edges: list[dict[str, Any]] = []
    for edge in graph.architecture_union_edges:
        source_module, destination = str(edge["source_module"]), str(edge["destination_module"])
        edges.append({
            "source_module": source_module,
            "destination_module": destination,
            "edge_kinds": sorted(str(kind) for kind in edge.get("edge_kinds", [])),
            "evidence": sorted(str(item) for item in edge.get("evidence", [])),
            "source_owner": owners.get(source_module, "UNKNOWN"),
            "destination_owner": owners.get(destination, "UNKNOWN"),
            "source_family": families.get(source_module, "UNKNOWN"),
            "destination_family": families.get(destination, "UNKNOWN"),
        })
    exports, broker_consumers, broker_edges = boundary_inventory(root, module_names)
    edges.extend({
        **edge,
        "source_owner": owners.get(edge["source_module"], "UNKNOWN"),
        "destination_owner": owners.get(edge["destination_module"], "UNKNOWN"),
        "source_family": families.get(edge["source_module"], "UNKNOWN"),
        "destination_family": families.get(edge["destination_module"], "UNKNOWN"),
    } for edge in broker_edges)
    edges.sort(key=lambda edge: (edge["source_module"], edge["destination_module"], edge["edge_kinds"], edge["evidence"]))

    return edges, exports, broker_consumers


def _phase0_dynamic_calls(root: Path, graph: Any, module_names: set[str]) -> list[dict[str, Any]]:
    dynamic_calls: list[dict[str, Any]] = []
    for row in graph.modules:
        module = str(row["module"])
        path = root / "src" / str(row["path"])
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.relative_to(root).as_posix())
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call = ast.unparse(node.func)
            if not (call.endswith("import_module") or call == "__import__"):
                continue
            try:
                literal = ast.literal_eval(node.args[0]) if node.args else None
            except (ValueError, TypeError):
                literal = None
            dynamic_calls.append({
                "source_module": module,
                "path": path.relative_to(root).as_posix(),
                "line": node.lineno,
                "call": call,
                "literal_target": literal if isinstance(literal, str) else None,
                "resolved_target": nearest_module(literal, module_names) if isinstance(literal, str) else None,
            })
    dynamic_calls.sort(key=lambda item: (item["path"], item["line"], item["call"]))

    return dynamic_calls


def _phase0_summary(root: Path, head: str, baseline_tree: str, raw_bytes: bytes, module_rows: list[dict[str, Any]], edges: list[dict[str, Any]], owners: dict[str, str], top_package_counts: dict[str, int], agent_package_counts: dict[str, int], exports: list[dict[str, Any]], broker_consumers: list[dict[str, Any]], dynamic_calls: list[dict[str, Any]], module_sccs: list[list[str]], cross_domain_sccs: list[list[str]], largest_families: int, cross_memberships: list[int], package_sccs: list[list[str]], forbidden: list[dict[str, Any]], forbidden_counts: dict[str, int], prefix_records: dict[str, dict[str, list[str]]], families: dict[str, str]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": {"id": GENERATOR_ID, "graph_engine": "scripts/w21_architecture.graph/imports@authority-tree", "schema_version": SCHEMA_VERSION},
        "owner_classification_source": "quality/architecture_w22_policy.json; observed placement map pending W23 CURRENT disposition",
        "canonicalization_version": CANONICALIZATION_VERSION,
        "baseline": {"commit": head, "tree": baseline_tree, "branch": _git(root, "branch", "--show-current")},
        "raw_graph": {"sha256": hashlib.sha256(raw_bytes).hexdigest(), "canonical_bytes": len(raw_bytes), "module_count": len(module_rows), "edge_count": len(edges)},
        "counts": {
            "production_modules": len(module_rows),
            "unclassified_modules": 0,
            "owner_counts": {owner: sum(value == owner for value in owners.values()) for owner in sorted(set(owners.values()))},
            "top_level_package_module_counts": dict(sorted(top_package_counts.items())),
            "agent_package_module_counts": dict(sorted(agent_package_counts.items())),
            "agent_modules": sum(value == "Agent" for value in owners.values()),
            "agent_boundary_exports": len(exports),
            "agent_boundary_production_consumers": sum(path.startswith("src/") for path in (row["path"] for row in broker_consumers)),
            "agent_boundary_test_consumers": sum(path.startswith("tests/") for path in (row["path"] for row in broker_consumers)),
            "dynamic_import_calls": len(dynamic_calls),
            "unresolved_dynamic_import_calls": sum(item["resolved_target"] is None for item in dynamic_calls),
            "module_sccs": len(module_sccs),
            "cross_domain_nontrivial_sccs": len(cross_domain_sccs),
            "largest_scc_modules": max((len(component) for component in module_sccs), default=0),
            "largest_scc_domain_family_count_B": largest_families,
            "required_final_threshold_T": max(5, largest_families // 2),
            "total_domain_family_membership_across_cross_domain_sccs": sum(cross_memberships),
            "package_family_sccs": len(package_sccs),
            "forbidden_owner_edge_counts": forbidden_counts,
            "forbidden_owner_edge_total": len(forbidden),
        },
        "cross_domain_sccs": [{"modules": component, "domain_families": sorted({families[name] for name in component})} for component in cross_domain_sccs],
        "package_family_sccs": package_sccs,
        "largest_module_scc": max(module_sccs, key=lambda component: (len(component), component), default=[]),
        "forbidden_owner_edge_examples": forbidden,
        "prefix_family_classification": prefix_records,
        "loc_class_boundaries": {"small_max": 100, "medium_max": 300, "large_min": 301},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    args = parser.parse_args()
    raw, summary = build_inventory(args.root)
    for destination, payload in ((args.raw_output, raw), (args.summary_output, summary)):
        path = destination if destination.is_absolute() else args.root / destination
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_bytes(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
