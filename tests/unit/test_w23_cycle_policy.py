"""Frozen W23 cycle threshold, lineage and complete-graph acceptance proof."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

import pytest

from scripts.check_current_architecture import (
    _current_broker_graph_errors,
    _dynamic_errors,
    _owner,
    _owner_edge_errors,
)
from scripts.w21_architecture import RepositorySource, build_graph
from scripts.w21_architecture.imports import collect_import_edges
from scripts.w23_architecture.evidence import verify_witness
from scripts.w23_architecture.phase0_inventory import domain_family, strongly_connected
from scripts.w23_tooling_successor import (
    project_historical_production,
    read_historical_retired_sources,
    read_historical_retirement_inputs,
    read_historical_tooling,
)

ROOT = Path(__file__).resolve().parents[2]
RECEIPT = "llm_agent.agent.reporting.run_receipt"
FINAL_RESPONSE = "llm_agent.agent.final_response"
PARTIAL_RESPONSE = "llm_agent.agent.reporting.partial_response"
RECEIPT_PATH = "src/llm_agent/agent/reporting/run_receipt.py"
BEFORE_SHA = "4da9479a213bf8142f8ce66577570f450b5d7416770afaff1c889d62f32fbe11"
BASELINE_SHA = "a37dd0481517b4f221b1df59faa72fbb41c980f85462efa2359b2e41e3915125"
PREDECESSOR_REPORT = "quality/w23/cycle_report_after.json"
PREDECESSOR_SHA = "e8f5236fd9ba8c5d20e5e384eef6bbce60b2b8009369890b13e7e39f7731485f"
PREDECESSOR_SOURCE_SHA = "4a5534cb6ccd4c16793db27b3272f019428cea848e65338f2811b5273e613cef"
PREDECESSOR_SEMANTIC_GRAPH_SHA = "d480275cdba0fa1342347bc15ec486998e1345375679f8180e9b95d1787737cb"
CURRENT_CYCLE_REPORT = "quality/w23/cycle_report_after_lock_ownership.json"
CURRENT_CYCLE_UNIT = "W23_GENERIC_LOCK_OWNERSHIP_CORRECTION"
RETIREMENT_CYCLE_REPORT = "quality/w23/cycle_report_after_compatibility_retirement.json"
COMPATIBILITY_CLOSURE_REPORT = "quality/w23/compatibility_closure_evidence.json"
RETIREMENT_CYCLE_REPORT_SHA = "ac8242d1d3cbbf065c7e9b041df05aa3b26cac86a0aeb86834126cb281f89ae1"
RETIREMENT_CYCLE_UNIT = "W23_PRODUCTION_COMPATIBILITY_RETIREMENT"
RETIREMENT_PREDECESSOR_SHA = "baba1da510914f2e8d355a4a983e97a7d3afccc66da341a479cb6783ec1e5389"
LOCK_SUCCESSOR_SHA = "baba1da510914f2e8d355a4a983e97a7d3afccc66da341a479cb6783ec1e5389"
RETIREMENT_ACTIVATION_RAW_SHA = "f7ce1f2a97aca6b740c55c501d6747739bf4730efd6a49b6c3b69867d7c7d1da"
RETIREMENT_ACTIVATION_NORMALIZED_SHA = "b86798f0bf79ed2bfd70b20daffd50be7d087048dcebe07a3bdcd2d05271fae4"
CURRENT_SELECTOR_SHA = "3cbe1b46c718abe681b3e9091c91dd46b686e148a85312b8e9a203651bc4bf25"
ACTIVATION_RAW_SHA = "5301052acb1bd89b9796332dbb0521ded26300a02476287e18acc2791681db15"
ACTIVATION_NORMALIZED_SHA = "aec518dbf5fcc5f54756aa125d17ab36ee5b5a834f27afa8f2d0eb8deb2e80de"
CANONICAL_LOCK_MODULES = {
    "llm_agent.process.instance_lock",
    "llm_agent.process.file_lock",
    "llm_agent.process.lock_record",
    "llm_agent.process.process_identity",
    "llm_agent.filesystem.lock_filesystem",
}
HISTORICAL_LOCK_MODULES = {
    "llm_agent.agent.runtime.instance_lock",
    "llm_agent.agent.runtime.file_lock",
    "llm_agent.agent.runtime.lock_record",
    "llm_agent.agent.runtime.process_identity",
    "llm_agent.agent.runtime.lock_filesystem",
}
LOCK_CHANGED_PRODUCTION_PATHS = {
    "src/llm_agent/agent/runtime/instance_lock.py",
    "src/llm_agent/agent/runtime/file_lock.py",
    "src/llm_agent/agent/runtime/lock_record.py",
    "src/llm_agent/agent/runtime/process_identity.py",
    "src/llm_agent/agent/runtime/lock_filesystem.py",
    "src/llm_agent/agent/application.py",
    "src/llm_agent/agent/application_wiring.py",
    "src/llm_agent/agent/checkpoint_manager.py",
    "src/llm_agent/agent/engineering/policy.py",
    "src/llm_agent/agent/engineering/recovery.py",
    "src/llm_agent/agent/engineering/store.py",
    "src/llm_agent/agent/engineering/summary.py",
    "src/llm_agent/agent/engineering/transactions.py",
    "src/llm_agent/agent/evaluation/feedback_store.py",
    "src/llm_agent/agent/runtime/home_lifecycle.py",
    "src/llm_agent/agent/runtime/state_migration.py",
    "src/llm_agent/application/workspace_recents.py",
    "src/llm_agent/discovery/store.py",
    "src/llm_agent/outputs/store.py",
}
LOCK_ADDED_PRODUCTION_PATHS = {
    "src/llm_agent/process/instance_lock.py",
    "src/llm_agent/process/file_lock.py",
    "src/llm_agent/process/lock_record.py",
    "src/llm_agent/process/process_identity.py",
    "src/llm_agent/filesystem/lock_filesystem.py",
}
RETIREMENT_PRODUCTION_PATHS = {
    "src/llm_agent/agent/memory/json_persistence.py",
    "src/llm_agent/agent/memory/path_safety.py",
    "src/llm_agent/agent/runtime/filesystem_primitives.py",
    "src/llm_agent/agent/runtime/legacy_paths.py",
    "src/llm_agent/agent/runtime/path_safety.py",
    "src/llm_agent/agent/runtime/workspace_trace_paths.py",
    "src/llm_agent/agent/tools/provenance.py",
    "src/llm_agent/agent/tools/process_tree.py",
    "src/llm_agent/agent/tools/stdio_streams.py",
    "src/llm_agent/agent/tools/stdio_launcher.py",
    "src/llm_agent/agent/tools/extension_catalog_codec.py",
    "src/llm_agent/agent/tools/extension_catalog_document.py",
    "src/llm_agent/agent/tools/extension_catalog_errors.py",
    "src/llm_agent/agent/tools/extension_catalog_lock.py",
    "src/llm_agent/agent/tools/extension_catalog_migration.py",
    "src/llm_agent/agent/tools/extension_catalog_service.py",
    "src/llm_agent/agent/tools/extension_catalog_storage.py",
    "src/llm_agent/agent/tools/extension_catalog_validation.py",
    "src/llm_agent/agent/tools/extension_manifest_parser.py",
    "src/llm_agent/agent/tools/extension_path.py",
    "src/llm_agent/agent/tools/extension_registry.py",
    "src/llm_agent/agent/tools/extension_state.py",
    "src/llm_agent/agent/tools/workspace_extensions_codec.py",
    "src/llm_agent/agent/tools/workspace_extensions_resolver.py",
    "src/llm_agent/agent/tools/workspace_extensions_service.py",
    "src/llm_agent/agent/tools/workspace_extensions_storage.py",
}
LOCK_DESTINATIONS = {
    "llm_agent.agent.runtime.instance_lock": "llm_agent.process.instance_lock",
    "llm_agent.agent.runtime.file_lock": "llm_agent.process.file_lock",
    "llm_agent.agent.runtime.lock_record": "llm_agent.process.lock_record",
    "llm_agent.agent.runtime.process_identity": "llm_agent.process.process_identity",
    "llm_agent.agent.runtime.lock_filesystem": "llm_agent.filesystem.lock_filesystem",
}
LOCK_CONSUMER_DESTINATIONS = {
    "llm_agent.agent.application": {"llm_agent.process.instance_lock"},
    "llm_agent.agent.application_wiring": {"llm_agent.process.instance_lock"},
    "llm_agent.agent.checkpoint_manager": {"llm_agent.filesystem.lock_filesystem"},
    "llm_agent.agent.engineering.policy": {"llm_agent.process.process_identity"},
    "llm_agent.agent.engineering.recovery": {"llm_agent.process.process_identity"},
    "llm_agent.agent.engineering.store": {
        "llm_agent.process.instance_lock",
        "llm_agent.process.process_identity",
    },
    "llm_agent.agent.engineering.summary": {"llm_agent.process.process_identity"},
    "llm_agent.agent.engineering.transactions": {
        "llm_agent.process.instance_lock",
        "llm_agent.process.process_identity",
        "llm_agent.filesystem.lock_filesystem",
    },
    "llm_agent.agent.evaluation.feedback_store": {"llm_agent.process.instance_lock"},
    "llm_agent.agent.runtime.home_lifecycle": {
        "llm_agent.process.file_lock",
        "llm_agent.process.process_identity",
        "llm_agent.filesystem.lock_filesystem",
    },
    "llm_agent.agent.runtime.state_migration": {"llm_agent.process.instance_lock"},
    "llm_agent.application.workspace_recents": {"llm_agent.process.instance_lock"},
    "llm_agent.discovery.store": {"llm_agent.process.instance_lock"},
    "llm_agent.outputs.store": {"llm_agent.process.instance_lock"},
}
F1 = sorted("llm_agent.agent." + name for name in (
    "application_result", "final_response", "interaction.transcript", "llm.session",
    "reporting.run_receipt", "reporting.run_receipt_builder", "reporting.run_snapshot",
    "routing.persona.current",
))
F2 = sorted("llm_agent.agent." + name for name in (
    "application", "application_wiring", "engineering.backends.evaluation", "engineering.model_safe",
    "engineering.policy", "engineering.recovery", "engineering.service", "engineering.store",
    "engineering.transactions", "evaluation.agent_executor", "evaluation.practical",
))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _edge_pairs(records: Iterable[dict[str, object]]) -> set[tuple[str, str]]:
    return {(str(record["source_module"]), str(record["destination_module"])) for record in records}


def _complete_graph(source: RepositorySource, policy: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    graph = build_graph(source)
    errors, _, broker_edges = _current_broker_graph_errors(source, source.module_paths(), policy)
    assert not errors, errors
    # Use the CURRENT augmentation even when its accepted export set is empty.
    return graph, list(graph.architecture_union_edges) + broker_edges


def _components(nodes: set[str], edges: list[dict[str, Any]]) -> list[list[str]]:
    nontrivial = strongly_connected(nodes, edges)
    assigned = {module for component in nontrivial for module in component}
    return sorted(nontrivial + [[name] for name in nodes - assigned], key=lambda c: (len(c), c))


def _vector(nodes: set[str], edges: list[dict[str, Any]], families: dict[str, str]) -> dict[str, Any]:
    components = strongly_connected(nodes, edges)
    cross = [c for c in components if len({families[m] for m in c}) > 1]
    cycle_edges = [e for e in edges if families[e["source_module"]] != families[e["destination_module"]]
                   and any(e["source_module"] in c and e["destination_module"] in c for c in cross)]
    counts = [len({families[m] for m in c}) for c in cross]
    return dict(modules=len(nodes), edges=len(edges), nontrivial_scc_count=len(components),
                largest_module_scc_size=max(map(len, components), default=0), cross_domain_scc_count=len(cross),
                cross_domain_cycle_edge_count=len(cycle_edges), largest_family_count=max(counts, default=0),
                total_cross_domain_family_membership=sum(counts),
                cross_domain_sccs=[dict(modules=c, families=sorted({families[m] for m in c})) for c in cross],
                cross_domain_cycle_edges=cycle_edges)


def _semantic_graph_sha(edges: list[dict[str, Any]]) -> str:
    semantic = sorted(
        [
            str(edge["source_module"]),
            str(edge["destination_module"]),
            sorted(str(kind) for kind in edge["edge_kinds"]),
        ]
        for edge in edges
    )
    return _sha(json.dumps(semantic, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))


def _production_inventory(source: RepositorySource) -> list[list[str]]:
    return sorted(
        [[source.relative(path), _sha(path.read_bytes())] for path in source.python_files()],
        key=lambda item: item[0],
    )


def _production_fingerprint(inventory: list[list[str]]) -> str:
    return _sha(json.dumps(inventory, separators=(",", ":")).encode("utf-8"))


def _inverse_lock_graph(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inverse = {canonical: historical for historical, canonical in LOCK_DESTINATIONS.items()}
    compatibility_edges = set(LOCK_DESTINATIONS.items())
    restored: list[dict[str, Any]] = []
    for edge in edges:
        source = str(edge["source_module"])
        destination = str(edge["destination_module"])
        if (source, destination) in compatibility_edges:
            continue
        restored.append(
            {
                **edge,
                "source_module": inverse.get(source, source),
                "destination_module": inverse.get(destination, destination),
            }
        )
    return restored


def _reachable(adjacency: dict[str, set[str]], source: str, destination: str) -> bool:
    pending = [source]
    visited: set[str] = set()
    while pending:
        module = pending.pop()
        if module == destination:
            return True
        if module not in visited:
            visited.add(module)
            pending.extend(adjacency.get(module, ()))
    return False


def _assert_successor_selection(path: str, report: dict[str, Any]) -> None:
    assert path == CURRENT_CYCLE_REPORT
    assert report["unit"] == CURRENT_CYCLE_UNIT
    assert report["predecessor"]["path"] == PREDECESSOR_REPORT
    assert report["predecessor"]["sha256"] == PREDECESSOR_SHA


def _assert_successor_freshness(
    selected: dict[str, Any],
    recomputed: dict[str, Any],
) -> None:
    assert selected == recomputed


def _assert_production_delta(
    before: list[list[str]],
    after: list[list[str]],
) -> dict[str, Any]:
    previous = dict(before)
    current = dict(after)
    previous_paths = set(previous)
    current_paths = set(current)
    changed = sorted(path for path in previous_paths & current_paths if previous[path] != current[path])
    added = sorted(current_paths - previous_paths)
    removed = sorted(previous_paths - current_paths)
    assert set(changed) == LOCK_CHANGED_PRODUCTION_PATHS
    assert set(added) == LOCK_ADDED_PRODUCTION_PATHS
    assert removed == []
    return {
        "changed_existing": [[path, previous[path], current[path]] for path in changed],
        "added": [[path, current[path]] for path in added],
        "removed": removed,
    }


def _pre_correction_source() -> RepositorySource:
    # Reverse only Design A in memory, authenticate exact precondition bytes, then
    # rebuild the complete graph. No historical output is relabeled as current.
    data = (ROOT / RECEIPT_PATH).read_bytes()
    imports = (
        b"from llm_agent.agent.reporting.partial_response import (\n"
        b"    compose_operational_answer as _compose_operational_answer,\n)\n"
        b"from llm_agent.agent.reporting.partial_response import (\n"
        b"    render_operational_answer as _render_operational_answer,\n)\n"
    )
    old = data.replace(imports, b"")
    old = old.replace(b"    answer_history = (\n", b"    from llm_agent.agent.final_response import compose_operational_answer\n    answer_history = (\n")
    old = old.replace(b"public_answer = _compose_operational_answer(", b"public_answer = compose_operational_answer(")
    old = old.replace(b"        _render_operational_answer,\n", b"")
    assert _sha(old) == BEFORE_SHA, "Production correction differs from exact approved Design A"
    source = RepositorySource(ROOT, "final-w22")
    source._trees[RECEIPT_PATH] = ast.parse(old, filename=RECEIPT_PATH)
    return source


def build_cycle_evidence() -> dict[str, Any]:
    """Recompute reproducible evidence directly from the implemented disk candidate."""
    baseline_bytes = (ROOT / "quality/w23/cycle_report_before.json").read_bytes()
    assert _sha(baseline_bytes) == BASELINE_SHA
    baseline = json.loads(baseline_bytes)
    assert baseline["largest_scc_domain_families_B"] == 6
    assert baseline["threshold_T"] == 5
    assert baseline["total_cross_domain_family_membership"] == 10
    assert sorted(row["modules"] for row in baseline["scc_memberships"]) == sorted([F1, F2])
    policy = json.loads((ROOT / "quality/architecture_current_policy.json").read_text())
    source = RepositorySource(ROOT, "final-w22")
    graph, edges = _complete_graph(source, policy)
    assert not _dynamic_errors(source, graph, policy)
    nodes = {row["module"] for row in graph.modules}
    owners = {name: _owner(name, policy) for name in nodes}
    assert all(owner is not None for owner in owners.values())
    assert not _owner_edge_errors(edges, policy)
    families = {name: domain_family(name, str(owners[name])) for name in nodes}
    before_graph, before_edges = _complete_graph(_pre_correction_source(), policy)
    assert {row["module"] for row in before_graph.modules} == nodes
    removed = sorted(_edge_pairs(before_edges) - _edge_pairs(edges))
    added = sorted(_edge_pairs(edges) - _edge_pairs(before_edges))
    assert removed == [(RECEIPT, FINAL_RESPONSE)]
    assert added == [(RECEIPT, PARTIAL_RESPONSE)]
    components = _components(nodes, edges)
    successors = [c for c in components if set(c) & set(F1)]
    assert set().union(*(set(c) for c in successors)) == set(F1)
    assert F2 in components
    assert F1 not in components
    vector = _vector(nodes, edges, families)
    assert vector["largest_family_count"] <= baseline["threshold_T"]
    assert vector["total_cross_domain_family_membership"] < baseline["total_cross_domain_family_membership"]
    membership = {m: c for c in components for m in c}
    lineage = [dict(baseline_family=family, modules=[m for m in F1 if families[m] == family],
                    successor_components=[c for c in successors if any(families[m] == family for m in c)])
               for family in sorted({families[m] for m in F1})]
    retained_edges = [e for e in edges if membership[e["source_module"]] == membership[e["destination_module"]]
                      and (len(membership[e["source_module"]]) > 1 or e["source_module"] == e["destination_module"])]
    pairs = sorted((source.relative(p), _sha(p.read_bytes())) for p in source.python_files())
    return dict(
        baseline_vector=dict(largest_family_count=6, total_cross_domain_family_membership=10,
                             cross_domain_scc_count=baseline["cross_domain_nontrivial_scc_count"]),
        threshold=dict(largest_family_count_max=5, total_membership_exclusive_max=10),
        pre_correction_vector=_vector(nodes, before_edges, families), final_vector=vector,
        baseline_components=dict(F1=dict(modules=F1, families=sorted({families[m] for m in F1})),
                                 F2=dict(modules=F2, families=sorted({families[m] for m in F2}))),
        F1_successor_components=[dict(modules=c, families=sorted({families[m] for m in c})) for c in successors],
        F1_family_lineage=lineage,
        F2_final_component=dict(modules=F2, families=sorted({families[m] for m in F2}), unchanged=True),
        edge_delta=dict(removed=removed, added=added), retained_cycle_edges=retained_edges,
        retained_nontrivial_components=strongly_connected(nodes, edges),
        production_source_fingerprint=dict(sha256=_sha(json.dumps(pairs, separators=(",", ":")).encode()),
                                           method="SHA-256 of compact JSON sorted [relative path, raw SHA-256] pairs; all production Python modules"),
        receipt_sha256=_sha((ROOT / RECEIPT_PATH).read_bytes()), baseline_report_sha256=BASELINE_SHA,
        verdict="PASS",
    )


@pytest.fixture(scope="module")
def evidence() -> dict[str, Any]:
    return json.loads((ROOT / CURRENT_CYCLE_REPORT).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def successor_report() -> dict[str, Any]:
    return json.loads((ROOT / CURRENT_CYCLE_REPORT).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def compatibility_retirement_successor_report() -> dict[str, Any]:
    return _build_compatibility_retirement_report()


def test_frozen_cycle_threshold_and_explicit_lineage(evidence: dict[str, Any]) -> None:
    vector = evidence["current_evidence"]["vector"]
    assert vector["largest_family_count"] <= 5
    assert vector["total_cross_domain_family_membership"] < 10
    successors = evidence["current_evidence"]["f1_successor_components"]
    assert all(len(row["families"]) == 1 for row in successors)
    assert set().union(*(set(row["modules"]) for row in successors)) == set(F1)
    assert evidence["current_evidence"]["f2_final_component"]["modules"] == F2


def test_receipt_cannot_reach_final_response_through_a_forwarder() -> None:
    source = RepositorySource(ROOT, "final-w22")
    policy = json.loads((ROOT / "quality/architecture_current_policy.json").read_text())
    _, edges = _complete_graph(source, policy)
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge["source_module"], set()).add(edge["destination_module"])
    pending, visited = [RECEIPT], set()
    while pending:
        node = pending.pop()
        if node not in visited:
            visited.add(node)
            pending.extend(adjacency.get(node, ()))
    assert FINAL_RESPONSE not in visited
    assert PARTIAL_RESPONSE in visited


@pytest.mark.parametrize(("text", "kind"), [
    ("def compose():\n    from llm_agent.agent.final_response import compose_operational_answer\n", "local_import"),
    ("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import llm_agent.agent.final_response\n", "type_checking"),
    ("import importlib\nx = importlib.import_module('llm_agent.agent.final_response')\n", "literal_dynamic_import"),
    ("from llm_agent.agent.final_response import compose_operational_answer\n__all__=['compose_operational_answer']\n", "reexport"),
])
def test_collector_does_not_hide_alternate_import_forms(text: str, kind: str) -> None:
    source = RepositorySource(ROOT, "final-w22")
    receipt_path = ROOT / ("src/llm_agent/agent/reporting/__init__.py" if kind == "reexport" else RECEIPT_PATH)
    source_module = "llm_agent.agent.reporting" if kind == "reexport" else RECEIPT
    target_path = ROOT / "src/llm_agent/agent/final_response.py"
    source._paths = {source_module: receipt_path, FINAL_RESPONSE: target_path}
    source._trees = {source.relative(receipt_path): ast.parse(text), source.relative(target_path): ast.parse("")}
    assert any(e.source_module == source_module and e.destination_module == FINAL_RESPONSE and e.edge_kind == kind
               for e in collect_import_edges(source))


def test_current_dynamic_policy_rejects_nonliteral_replacement() -> None:
    source = RepositorySource(ROOT, "final-w22")
    path = ROOT / RECEIPT_PATH
    source._paths = {RECEIPT: path}
    source._trees[RECEIPT_PATH] = ast.parse("from importlib import import_module\ndef compose(target):\n    return import_module(target)\n")
    graph = SimpleNamespace(architecture_union_edges=[], declarative_edges=[])
    policy = json.loads((ROOT / "quality/architecture_current_policy.json").read_text())
    errors = _dynamic_errors(source, graph, policy)
    assert any(RECEIPT in error and "non-literal" in error for error in errors)


def _build_lock_successor_report(authorization_override: dict[str, Any] | None = None) -> dict[str, Any]:
    path = ROOT / CURRENT_CYCLE_REPORT
    seed = json.loads(path.read_text(encoding="utf-8"))
    authorization = authorization_override or seed["authorization"]
    activation = authorization["normalized_full_text"]
    normalized = activation.replace("\r\n", "\n").replace("\r", "\n")
    assert _sha(normalized.encode("utf-8")) == ACTIVATION_NORMALIZED_SHA
    assert authorization["raw_sha256"] == ACTIVATION_RAW_SHA
    assert authorization["decision_source"] == "USER"
    assert authorization["unit"] == CURRENT_CYCLE_UNIT
    predecessor_bytes = (ROOT / PREDECESSOR_REPORT).read_bytes()
    assert _sha(predecessor_bytes) == PREDECESSOR_SHA
    predecessor = json.loads(predecessor_bytes)
    predecessor_inventory = seed["predecessor_source_inventory"]
    assert _production_fingerprint(predecessor_inventory) == PREDECESSOR_SOURCE_SHA

    policy = json.loads((ROOT / "quality/architecture_current_policy.json").read_text())
    source = RepositorySource(ROOT, "final-w22")
    graph, edges = _complete_graph(source, policy)
    assert not _dynamic_errors(source, graph, policy)
    nodes = {row["module"] for row in graph.modules}
    assert len(nodes) == 909 and len(edges) == 3150
    owners = {name: _owner(name, policy) for name in nodes}
    assert all(owner is not None for owner in owners.values())
    families = {name: domain_family(name, str(owners[name])) for name in nodes}
    current_vector = _vector(nodes, edges, families)
    assert current_vector["largest_family_count"] <= 5
    assert current_vector["total_cross_domain_family_membership"] < 10
    assert current_vector["largest_family_count"] == 4
    assert current_vector["total_cross_domain_family_membership"] == 4
    components = _components(nodes, edges)
    assert F2 in components and F1 not in components
    f1_successors = [component for component in components if set(component) & set(F1)]
    assert set().union(*(set(component) for component in f1_successors)) == set(F1)
    def families_for(component: list[str]) -> list[str]:
        return sorted({families[module] for module in component})

    assert all(len(families_for(component)) == 1 for component in f1_successors)

    # Exactly five historical facade edges are the reversible ownership projection.
    edge_pairs = _edge_pairs(edges)
    compatibility = set(LOCK_DESTINATIONS.items())
    assert compatibility <= edge_pairs
    assert not any(destination in HISTORICAL_LOCK_MODULES for _, destination in edge_pairs)
    for canonical in CANONICAL_LOCK_MODULES:
        assert _owner(canonical, policy) == "Platform"
        module_path = source.module_paths().get(canonical)
        assert module_path is not None
        assert not any(edge.source_module == canonical and edge.destination_module.startswith("llm_agent.agent")
                       for edge in collect_import_edges(source))
    import_edges = collect_import_edges(source)
    for consumer, destinations in LOCK_CONSUMER_DESTINATIONS.items():
        actual = {edge.destination_module for edge in import_edges if edge.source_module == consumer
                  and edge.destination_module in CANONICAL_LOCK_MODULES}
        assert actual == destinations, (consumer, actual, destinations)
    historical_imports = sorted(
        [edge.source_module, edge.destination_module]
        for edge in import_edges
        if edge.source_module in LOCK_CONSUMER_DESTINATIONS
        and edge.destination_module in HISTORICAL_LOCK_MODULES
    )
    platform_to_agent = sorted(
        [source_module, destination_module]
        for source_module, destination_module in edge_pairs
        if source_module in CANONICAL_LOCK_MODULES
        and destination_module.startswith("llm_agent.agent")
    )
    assert historical_imports == []
    assert platform_to_agent == []
    inverse_edges = _inverse_lock_graph(edges)
    inverse_signature = _semantic_graph_sha(inverse_edges)
    assert inverse_signature == PREDECESSOR_SEMANTIC_GRAPH_SHA
    inverse_pairs = _edge_pairs(inverse_edges)

    current_inventory = _production_inventory(source)
    delta = _assert_production_delta(predecessor_inventory, current_inventory)
    semantic_sha = _semantic_graph_sha(edges)
    production_sha = _production_fingerprint(current_inventory)
    input_paths = (
        "scripts/w21_architecture/graph.py",
        "scripts/w21_architecture/imports.py",
        "scripts/w21_architecture/source.py",
        "scripts/w23_architecture/phase0_inventory.py",
        "scripts/check_current_architecture.py",
        "quality/architecture_current_policy.json",
        "tests/unit/test_w23_cycle_policy.py",
        "quality/w23/cycle_report_before.json",
    )
    protected_paths = sorted(path for path in predecessor["protected_production_sha256"]
                             if path.startswith("src/"))
    protected_hashes = {relative: _sha((ROOT / relative).read_bytes()) for relative in protected_paths}
    protected_hashes.update({path: digest for path, digest in predecessor["protected_production_sha256"].items()
                             if not path.startswith("src/")})
    recorded_inputs = json.loads((ROOT / CURRENT_CYCLE_REPORT).read_bytes())["input_sha256"]
    input_hashes = dict(recorded_inputs)
    input_hashes.update({relative: _sha((ROOT / relative).read_bytes()) for relative in input_paths})
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        adjacency.setdefault(str(edge["source_module"]), set()).add(str(edge["destination_module"]))
    assert not _reachable(adjacency, RECEIPT, FINAL_RESPONSE)

    return {
        "schema_version": 1,
        "artifact": CURRENT_CYCLE_REPORT,
        "unit": CURRENT_CYCLE_UNIT,
        "verdict": "PASS",
        "authority": {
            **predecessor["authority"],
            "user_activation_sha256": authorization["normalized_sha256"],
        },
        "candidate": {
            **predecessor["candidate"],
            "staging": "EMPTY",
            "source_fingerprint_sha256": production_sha,
        },
        "predecessor": {
            "path": PREDECESSOR_REPORT,
            "sha256": PREDECESSOR_SHA,
            "production_fingerprint_sha256": PREDECESSOR_SOURCE_SHA,
            "semantic_graph_sha256": PREDECESSOR_SEMANTIC_GRAPH_SHA,
            "modules": 904,
            "edges": 3145,
            "largest_family_count": 4,
            "total_cross_domain_family_membership": 4,
            "f1_modules": F1,
            "f2_modules": F2,
            "historical_vector": predecessor["actual_evidence"]["final_vector"],
            "candidate_identity": predecessor["candidate"],
        },
        "authorization": authorization,
        "baseline": {
            "path": "quality/w23/cycle_report_before.json",
            "sha256": BASELINE_SHA,
            "largest_family_count": 6,
            "total_cross_domain_family_membership": 10,
        },
        "thresholds": {"largest_family_count_max": 5, "total_membership_exclusive_max": 10},
        "predecessor_source_inventory": predecessor_inventory,
        "current_evidence": {
            "modules": len(nodes),
            "edges": len(edges),
            "production_fingerprint_sha256": production_sha,
            "semantic_graph_sha256": semantic_sha,
            "vector": current_vector,
            "cross_domain_sccs": current_vector["cross_domain_sccs"],
            "cross_domain_cycle_edges": current_vector["cross_domain_cycle_edges"],
            "f1_successor_components": [
                {"modules": component, "families": families_for(component)} for component in f1_successors
            ],
            "f1_continuity": True,
            "f1_reconnected": False,
            "f2_final_component": {"modules": F2, "families": families_for(F2), "unchanged": True},
            "receipt_reaches_final_response": False,
            "historical_production_imports": historical_imports,
            "platform_to_agent_lock_edges": platform_to_agent,
        },
        "protected_production_sha256": protected_hashes,
        "unit_delta": {
            **delta,
            "module_edge_delta": {
                "removed": [list(pair) for pair in sorted(inverse_pairs - edge_pairs)],
                "added": [list(pair) for pair in sorted(edge_pairs - inverse_pairs)],
            },
            "inverse_projection_signature": {
                "predecessor_semantic_graph_sha256": PREDECESSOR_SEMANTIC_GRAPH_SHA,
                "reconstructed_semantic_graph_sha256": inverse_signature,
            },
        },
        "compatibility_projection_edges": [list(pair) for pair in sorted(compatibility)],
        "input_sha256": input_hashes,
        "methodology": {
            "production_fingerprint": "SHA-256 of compact JSON sorted [relative path, raw SHA-256] pairs for all production Python modules",
            "semantic_graph_signature": "SHA-256 of compact JSON sorted [source_module, destination_module, sorted(edge_kinds)] records; ensure_ascii=True; no trailing LF",
            "predecessor": "Immutable historical report is authenticated by raw SHA-256 and complete sorted production inventory",
            "successor": "Recomputed from current disk source using RepositorySource final-w22, complete graph and CURRENT augmentation",
        },
    }


def _retirement_predecessor_inventory(report: dict[str, Any]) -> list[list[str]]:
    inventory = {path: digest for path, digest in report["predecessor_source_inventory"]}
    for path, before, after in report["unit_delta"]["changed_existing"]:
        assert inventory.get(path) == before
        inventory[path] = after
    for path, digest in report["unit_delta"]["added"]:
        assert path not in inventory
        inventory[path] = digest
    return [[path, inventory[path]] for path in sorted(inventory)]


def _retirement_predecessor_source() -> RepositorySource:
    source = RepositorySource(ROOT, "final-w22")
    source.module_paths()
    archived = read_historical_retired_sources(ROOT)
    for relative in sorted(RETIREMENT_PRODUCTION_PATHS):
        path = ROOT / relative
        module = "llm_agent." + relative.removeprefix("src/llm_agent/").removesuffix(".py").replace("/", ".")
        assert not path.exists()
        blob = archived[relative]
        source._paths[module] = path
        source._trees[relative] = ast.parse(blob.decode("utf-8"), filename=relative)
    return source


def _build_compatibility_retirement_report() -> dict[str, Any]:
    """Recompute historical retirement through an exact authenticated text-only projection."""

    predecessor_path = "quality/w23/cycle_report_after_lock_ownership.json"
    predecessor_bytes = (ROOT / predecessor_path).read_bytes()
    assert _sha(predecessor_bytes) == RETIREMENT_PREDECESSOR_SHA
    predecessor = json.loads(predecessor_bytes)
    predecessor_inventory = _retirement_predecessor_inventory(predecessor)
    assert len(predecessor_inventory) == 909
    assert _production_fingerprint(predecessor_inventory) == predecessor["candidate"]["source_fingerprint_sha256"]

    live_inventory = _production_inventory(RepositorySource(ROOT, "final-w22"))
    current_inventory = project_historical_production(ROOT, live_inventory)
    before, after = dict(predecessor_inventory), dict(current_inventory)
    assert sorted(set(before) - set(after)) == sorted(RETIREMENT_PRODUCTION_PATHS)
    assert sorted(set(after) - set(before)) == []
    assert sorted(path for path in set(before) & set(after) if before[path] != after[path]) == []
    assert all((ROOT / path).is_file() for path in after)
    assert all(not (ROOT / path).exists() for path in RETIREMENT_PRODUCTION_PATHS)

    policy = json.loads((ROOT / "quality/architecture_current_policy.json").read_text(encoding="utf-8"))
    source = RepositorySource(ROOT, "final-w22")
    graph, edges = _complete_graph(source, policy)
    assert not _dynamic_errors(source, graph, policy)
    assert not _owner_edge_errors(edges, policy)
    predecessor_source = _retirement_predecessor_source()
    predecessor_graph, predecessor_edges = _complete_graph(predecessor_source, policy)
    assert len(predecessor_graph.modules) == 909 and len(predecessor_edges) == 3150
    assert _semantic_graph_sha(predecessor_edges) == predecessor["current_evidence"]["semantic_graph_sha256"]
    assert _production_fingerprint(predecessor_inventory) == predecessor["candidate"]["source_fingerprint_sha256"]

    nodes = {row["module"] for row in graph.modules}
    owners = {name: _owner(name, policy) for name in nodes}
    assert all(owner is not None for owner in owners.values())
    families = {name: domain_family(name, str(owners[name])) for name in nodes}
    vector = _vector(nodes, edges, families)
    components = _components(nodes, edges)
    assert F2 in components and F1 not in components
    f1_successors = [component for component in components if set(component) & set(F1)]
    assert set().union(*(set(component) for component in f1_successors)) == set(F1)
    assert all(len({families[name] for name in component}) == 1 for component in f1_successors)
    assert vector["largest_family_count"] == 4
    assert vector["total_cross_domain_family_membership"] == 4
    assert len(nodes) == 883 and len(edges) == 3124

    old_pairs, new_pairs = _edge_pairs(predecessor_edges), _edge_pairs(edges)
    removed_edges, added_edges = sorted(old_pairs - new_pairs), sorted(new_pairs - old_pairs)
    assert not added_edges
    authorization = {
        "attachment_name": "W23 v007 ACTIVATE BOUNDED PRODUCTION COMPATIBILITY RETIREMENT 26 HISTORICAL FACADES + PRODUCTION SUCCESSOR EVIDENCE",
        "decision_source": "USER",
        "raw_sha256": RETIREMENT_ACTIVATION_RAW_SHA,
        "normalized_sha256": RETIREMENT_ACTIVATION_NORMALIZED_SHA,
    }
    baseline_sha = predecessor["baseline"]["sha256"]
    input_paths = (
        "scripts/w21_architecture/graph.py",
        "scripts/w21_architecture/imports.py",
        "scripts/w21_architecture/source.py",
        "scripts/w23_architecture/phase0_inventory.py",
        "scripts/check_current_architecture.py",
        "scripts/check_w21_compatibility.py",
        "quality/architecture_current_policy.json",
        "quality/architecture_compatibility.json",
        "quality/w23/disposition_amendments.json",
        "tests/unit/test_current_architecture.py",
        "tests/unit/runtime/test_wave21_compatibility.py",
        "tests/unit/test_w23_cycle_policy.py",
        "quality/w23/cycle_report_before.json",
    )
    protected = predecessor["protected_production_sha256"]
    protected_hashes = dict(protected)
    historical_production = dict(after)
    historical_production.update({path: _sha(data) for path, data in read_historical_retired_sources(ROOT).items()})
    recomputed_protected = {path: historical_production[path] for path in sorted(protected)
                            if path.startswith("src/")}
    assert all(protected_hashes[path] == digest for path, digest in recomputed_protected.items())
    protected_hashes.update(recomputed_protected)
    recorded_inputs = json.loads((ROOT / RETIREMENT_CYCLE_REPORT).read_bytes())["input_sha256"]
    input_hashes = dict(recorded_inputs)
    historical_tooling = read_historical_retirement_inputs(ROOT)
    baseline_bytes = historical_tooling["quality/w23/cycle_report_before.json"]
    assert _sha(baseline_bytes) == BASELINE_SHA
    input_hashes.update({path: _sha(historical_tooling[path]) for path in input_paths})
    removed_modules = sorted("llm_agent." + path.removeprefix("src/llm_agent/").removesuffix(".py").replace("/", ".") for path in RETIREMENT_PRODUCTION_PATHS)
    return {
        "schema_version": 1,
        "artifact": RETIREMENT_CYCLE_REPORT,
        "unit": RETIREMENT_CYCLE_UNIT,
        "verdict": "PASS",
        "authority": {**predecessor["authority"], "user_activation_sha256": RETIREMENT_ACTIVATION_NORMALIZED_SHA},
        "candidate": {
            **predecessor["candidate"],
            "staging": "EMPTY",
            "source_fingerprint_sha256": _production_fingerprint(current_inventory),
        },
        "predecessor": {
            "path": predecessor_path,
            "sha256": RETIREMENT_PREDECESSOR_SHA,
            "production_fingerprint_sha256": _production_fingerprint(predecessor_inventory),
            "semantic_graph_sha256": _semantic_graph_sha(predecessor_edges),
            "modules": 909,
            "edges": 3150,
            "largest_family_count": 4,
            "total_cross_domain_family_membership": 4,
            "f1_modules": F1,
            "f2_modules": F2,
        },
        "authorization": authorization,
        "baseline": {"path": "quality/w23/cycle_report_before.json", "sha256": baseline_sha},
        "predecessor_production_inventory_sha256": _production_fingerprint(predecessor_inventory),
        "current_evidence": {
            "modules": len(nodes),
            "edges": len(edges),
            "production_fingerprint_sha256": _production_fingerprint(current_inventory),
            "semantic_graph_sha256": _semantic_graph_sha(edges),
            "vector": vector,
            "f1_successor_components": [
                {"modules": component, "families": sorted({families[name] for name in component})}
                for component in f1_successors
            ],
            "f1_reconnected": False,
            "f2_final_component": {"modules": F2, "families": sorted({families[name] for name in F2}), "unchanged": True},
            "retired_modules_absent": removed_modules,
            "retired_incoming_production_edges": [],
            "broker_absent": "llm_agent.application.agent_boundary" not in source.module_paths(),
        },
        "protected_production_sha256": protected_hashes,
        "unit_delta": {
            "changed_existing": [],
            "added": [],
            "removed": sorted(RETIREMENT_PRODUCTION_PATHS),
            "module_edge_delta": {"removed": [list(pair) for pair in removed_edges], "added": []},
        },
        "input_sha256": input_hashes,
        "methodology": {
            "production_fingerprint": "SHA-256 of compact JSON sorted [relative path, raw SHA-256] pairs for all current production Python modules",
            "predecessor": "Reconstructed the accepted lock successor inventory by applying its recorded delta to its immutable predecessor inventory; verified its complete source graph using authenticated HEAD blobs for the 26 retired modules",
            "successor": "Recomputed from current disk source using RepositorySource final-w22, complete graph and CURRENT augmentation",
        },
    }


def test_predecessor_report_remains_immutable_and_authentic() -> None:
    data = (ROOT / PREDECESSOR_REPORT).read_bytes()
    assert _sha(data) == PREDECESSOR_SHA
    report = json.loads(data)
    assert report["actual_evidence"]["production_source_fingerprint"]["sha256"] == PREDECESSOR_SOURCE_SHA
    assert report["actual_evidence"]["final_vector"]["modules"] == 904
    assert report["actual_evidence"]["final_vector"]["edges"] == 3145
    assert report["actual_evidence"]["final_vector"]["largest_family_count"] == 4
    assert report["actual_evidence"]["final_vector"]["total_cross_domain_family_membership"] == 4
    assert report["actual_evidence"]["F1_successor_components"]
    assert report["actual_evidence"]["F2_final_component"]["modules"] == F2


def test_successor_report_is_explicit_fresh_and_attributed(
    evidence: dict[str, Any], successor_report: dict[str, Any]
) -> None:
    report = json.loads((ROOT / CURRENT_CYCLE_REPORT).read_text(encoding="utf-8"))
    assert _sha((ROOT / CURRENT_CYCLE_REPORT).read_bytes()) == LOCK_SUCCESSOR_SHA
    _assert_successor_selection(CURRENT_CYCLE_REPORT, report)
    assert {"schema_version", "artifact", "authority", "authorization", "candidate",
            "predecessor", "predecessor_source_inventory", "baseline", "thresholds",
            "current_evidence", "protected_production_sha256", "unit_delta", "input_sha256",
            "methodology", "verdict"} <= set(report)
    assert report["artifact"] == CURRENT_CYCLE_REPORT
    assert report["current_evidence"]["modules"] == 909
    assert report["current_evidence"]["edges"] == 3150
    assert report["unit_delta"]["removed"] == []
    assert len(report["unit_delta"]["changed_existing"]) == 19
    assert len(report["unit_delta"]["added"]) == 5
    assert evidence["verdict"] == "PASS"


@pytest.mark.parametrize(
    ("path", "mutation"),
    [
        ("current_evidence.production_fingerprint_sha256", "stale fingerprint"),
        ("current_evidence.vector.edges", "stale graph metrics"),
        ("protected_production_sha256", "stale protected hashes"),
        ("unit_delta.changed_existing", "unauthorized changed path"),
        ("unit_delta.added", "unauthorized added path"),
        ("current_evidence.f1_reconnected", "F1 reconnection"),
        ("current_evidence.receipt_reaches_final_response", "receipt reachability"),
        ("current_evidence.f2_final_component.modules", "F2 expansion"),
        ("current_evidence.f2_final_component.modules", "F2 contraction"),
        ("unit_delta.inverse_projection_signature.reconstructed_semantic_graph_sha256", "wrong inverse signature"),
        ("current_evidence.historical_production_imports", "historical production import"),
        ("current_evidence.platform_to_agent_lock_edges", "Platform to Agent edge"),
    ],
)
def test_mutated_successor_evidence_is_rejected(
    successor_report: dict[str, Any], path: str, mutation: str
) -> None:
    selected = copy.deepcopy(successor_report)
    target: dict[str, Any] = selected
    keys = path.split(".")
    for key in keys[:-1]:
        target = target[key]
    key = keys[-1]
    original = target[key]
    if mutation == "F2 expansion":
        target[key] = [*original, "llm_agent.agent.unexpected"]
    elif mutation == "F2 contraction":
        target[key] = original[:-1]
    elif isinstance(original, list):
        target[key] = [*original, "unauthorized.synthetic.path"]
    elif isinstance(original, dict):
        target[key] = {**original, "synthetic": "stale"}
    elif isinstance(original, bool):
        target[key] = not original
    elif isinstance(original, int):
        target[key] = original + 1
    else:
        target[key] = "stale"
    with pytest.raises(AssertionError):
        _assert_successor_freshness(selected, successor_report)


@pytest.mark.parametrize(
    ("selected_path", "field"),
    [
        ("quality/w23/cycle_report_after.json", None),
        (CURRENT_CYCLE_REPORT, "unit"),
        (CURRENT_CYCLE_REPORT, "predecessor"),
    ],
)
def test_successor_selection_rejects_fallback_and_wrong_lineage(
    successor_report: dict[str, Any], selected_path: str, field: str | None
) -> None:
    selected = copy.deepcopy(successor_report)
    if field == "unit":
        selected["unit"] = "OTHER_UNIT"
    elif field == "predecessor":
        selected["predecessor"]["sha256"] = "0" * 64
    with pytest.raises(AssertionError):
        _assert_successor_selection(selected_path, selected)


def test_historical_compatibility_retirement_successor_is_explicit_and_exact(
    compatibility_retirement_successor_report: dict[str, Any],
) -> None:
    path = RETIREMENT_CYCLE_REPORT
    raw = (ROOT / path).read_bytes()
    selected = json.loads(raw)
    assert _sha(raw) == RETIREMENT_CYCLE_REPORT_SHA
    assert path == selected["artifact"]
    assert selected["unit"] == RETIREMENT_CYCLE_UNIT
    assert selected["predecessor"]["path"] == "quality/w23/cycle_report_after_lock_ownership.json"
    assert selected["predecessor"]["sha256"] == RETIREMENT_PREDECESSOR_SHA
    assert selected["authorization"]["decision_source"] == "USER"
    assert selected["authorization"]["raw_sha256"] == RETIREMENT_ACTIVATION_RAW_SHA
    assert selected["authorization"]["normalized_sha256"] == RETIREMENT_ACTIVATION_NORMALIZED_SHA
    assert selected["unit_delta"]["removed"] == sorted(RETIREMENT_PRODUCTION_PATHS)
    assert selected["unit_delta"]["added"] == []
    assert selected["unit_delta"]["changed_existing"] == []
    assert selected["current_evidence"]["modules"] == 883
    assert selected["current_evidence"]["edges"] == 3124
    assert selected["current_evidence"]["vector"]["largest_family_count"] == 4
    assert selected["current_evidence"]["vector"]["total_cross_domain_family_membership"] == 4
    assert selected["current_evidence"]["f1_reconnected"] is False
    assert selected["current_evidence"]["f2_final_component"]["modules"] == F2
    assert selected["current_evidence"]["retired_incoming_production_edges"] == []
    assert selected["current_evidence"]["broker_absent"] is True
    assert selected["verdict"] == "PASS"
    recomputed = compatibility_retirement_successor_report
    assert selected["current_evidence"]["production_fingerprint_sha256"] == recomputed["current_evidence"]["production_fingerprint_sha256"]
    assert selected["current_evidence"]["semantic_graph_sha256"] == recomputed["current_evidence"]["semantic_graph_sha256"]
    assert selected["current_evidence"]["vector"] == recomputed["current_evidence"]["vector"]
    assert selected["unit_delta"]["removed"] == recomputed["unit_delta"]["removed"]
    assert selected["unit_delta"]["added"] == recomputed["unit_delta"]["added"]
    assert selected["unit_delta"]["changed_existing"] == recomputed["unit_delta"]["changed_existing"]


def test_historical_compatibility_witness_continues_accepted_production() -> None:
    raw_witness = (ROOT / COMPATIBILITY_CLOSURE_REPORT).read_bytes()
    witness = json.loads(raw_witness.decode("utf-8"))
    assert raw_witness.endswith(b"\n") and not raw_witness.startswith(b"\xef\xbb\xbf")
    assert raw_witness == (json.dumps(witness, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    successor_bytes = (ROOT / RETIREMENT_CYCLE_REPORT).read_bytes()
    successor = json.loads(successor_bytes)
    selected = witness["selected_production_evidence"]
    assert selected["path"] == RETIREMENT_CYCLE_REPORT
    assert selected["sha256"] == _sha(successor_bytes)
    assert witness["production_identity"]["production_fingerprint_sha256"] == successor["current_evidence"]["production_fingerprint_sha256"]
    assert witness["production_identity"]["semantic_graph_sha256"] == successor["current_evidence"]["semantic_graph_sha256"]
    assert witness["production_identity"]["modules"] == 883
    assert witness["production_identity"]["edges"] == 3124
    dispositions = witness["support_dispositions"]
    required_fields = {"id", "authenticated_record_ids", "module", "symbols", "canonical_owner", "presence", "classification", "production_consumers", "test_consumers", "installed_probe_consumers", "package_reexports", "support_basis", "persisted_identity_requirement"}
    assert all(required_fields <= set(item) for item in dispositions)
    classifications = {item["classification"] for item in dispositions}
    assert classifications == {"DURABLE_COMPATIBILITY_RETAIN", "FUNCTIONAL_ADAPTER_RETAIN", "TEMPORARY_BOUNDED_PROJECTION_RETAIN", "INTERNAL_HISTORICAL_REMOVE", "RETIRED_ALREADY", "CANONICAL_IMPLEMENTATION_NOT_COMPATIBILITY"}
    temporary = [item for item in dispositions if item["classification"] == "TEMPORARY_BOUNDED_PROJECTION_RETAIN"]
    assert len(temporary) == 13
    assert all({"exact_consumer", "reason", "canonical_owner", "retirement_condition", "forbidden_new_consumers"} <= set(item) for item in temporary)
    assert sum(item["classification"] == "INTERNAL_HISTORICAL_REMOVE" for item in dispositions) == 26
    assert witness["graph_engine_disposition"]["decision"] == "GRAPH_ENGINE_RETENTION_JUSTIFIED"
    assert witness["graph_engine_disposition"]["production_or_engine_move_required"] is False
    additive_path = "quality/w23/acceptance_tooling_witness_v001.json"
    additive_bytes = (ROOT / additive_path).read_bytes()
    additive = json.loads(additive_bytes.decode("utf-8"))
    tooling = read_historical_tooling(ROOT)
    snapshots = {row["path"]: (ROOT / row["path"]).read_bytes()
                 for row in additive["historical_tooling_snapshots"]}
    verified = verify_witness(
        additive_bytes,
        raw_witness,
        successor_bytes,
        tooling,
        snapshots,
    )
    assert verified["unit"] == "W23_ACCEPTANCE_TOOLING_SUCCESSOR"


@pytest.mark.parametrize("field", ["current_evidence", "unit_delta", "input_sha256", "authorization"])
def test_compatibility_retirement_successor_mutation_is_rejected(
    compatibility_retirement_successor_report: dict[str, Any], field: str
) -> None:
    selected = copy.deepcopy(compatibility_retirement_successor_report)
    selected[field] = {**selected[field], "synthetic": "stale"}
    recomputed = _build_compatibility_retirement_report()
    with pytest.raises(AssertionError):
        _assert_successor_freshness(selected, recomputed)


def test_predecessor_hash_pin_rejects_synthetic_byte_mutation() -> None:
    original = (ROOT / PREDECESSOR_REPORT).read_bytes()
    assert _sha(original) == PREDECESSOR_SHA
    assert _sha(original + b" ") != PREDECESSOR_SHA
