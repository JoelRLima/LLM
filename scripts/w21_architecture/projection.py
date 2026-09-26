"""Canonical semantic projections of frozen Wave 21 authority documents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .graph import _module_identity, _scc_count, _semantic_hash, package_family

POLICY_FIELDS = (
    "edge_classes",
    "classification_semantics",
    "preserved_canonical_owners",
    "approved_neutral_owners",
    "explicit_canonical_directions",
    "selector_schema",
    "neutral_owner_invariants",
    "residual_baseline_dependency_policy",
    "rules",
    "lane_acceptance_architecture",
    "disguised_cycle_policy",
)
COMPATIBILITY_FIELDS = (
    "bridges",
    "adapter_edges",
    "bridge_semantics",
    "forbidden_compatibility_expansion",
)


def _project(document: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    missing = [field for field in fields if field not in document]
    if missing:
        raise KeyError(f"authority document is missing projection fields: {', '.join(missing)}")
    return {field: document[field] for field in fields}


def project_policy(document: Mapping[str, Any]) -> dict[str, Any]:
    return _project(document, POLICY_FIELDS)


def project_compatibility(document: Mapping[str, Any]) -> dict[str, Any]:
    return _project(document, COMPATIBILITY_FIELDS)


_BASELINE_SOURCE_HASHES = {
    "current_dependency_graph_sha256": "3e04ec2f40a1a8bd7e4a176b2652dcdf60d80325cc1bcdee8a0465e1151b7b14",
    "transition_violations_sha256": "d09e71ab2e75514351491e7ab8e2c16ab5e9983b06009ae91021e4d60fa9458d",
    "char_path_001_sha256": "ad100adc710cfa3b58a55b8dca66d59c9d39aa4313a5f7b87ae263e27e3923e3",
}


def _graph_signatures(document: Mapping[str, Any]) -> dict[str, Any]:
    modules = list(document["modules"])
    static = list(document["module_edges"])
    union = list(document["architecture_union_edges"])
    static_packages = list(document["package_edges"])
    union_packages = list(document["architecture_union_package_edges"])
    module_names = tuple(str(item["module"]) for item in modules)
    return {
        "production_module_count": len(modules),
        "module_identity_sha256": _module_identity(modules),
        "static_module_edge_count": len(static),
        "static_module_edge_semantic_sha256": _semantic_hash(static),
        "architecture_union_edge_count": len(union),
        "architecture_union_edge_semantic_sha256": _semantic_hash(union),
        "static_package_edge_count": len(static_packages),
        "architecture_union_package_edge_count": len(union_packages),
        "static_nontrivial_scc_count": _scc_count(module_names, static),
        "architecture_union_nontrivial_scc_count": _scc_count(module_names, union),
    }


def _baseline_pairs(document: Mapping[str, Any]) -> list[dict[str, str]]:
    pairs = {
        (str(edge["source_module"]), str(edge["destination_module"]))
        for edge in document["architecture_union_edges"]
        if package_family(str(edge["source_module"])) != package_family(str(edge["destination_module"]))
    }
    return [{"source_module": source, "destination_module": destination} for source, destination in sorted(pairs)]


def _legacy_surface(document: Mapping[str, Any]) -> dict[str, Any]:
    symbols = sorted(str(value) for value in document["legacy_surface"])
    consumers: list[dict[str, str]] = []
    for item in document["consumer_classifications"]:
        path = str(item["consumer"])
        module = path.removesuffix(".py").replace("/", ".")
        consumers.append({
            "consumer_module": module,
            "consumer_path": path,
            "symbol": str(item["symbol"]),
            "classification": str(item["classification"]),
        })
    consumers.sort(key=lambda item: (item["consumer_module"], item["symbol"]))
    return {
        "symbols": symbols,
        "baseline_active_consumer_count": len(consumers),
        "baseline_active_consumers": consumers,
        "new_consumers": "forbidden",
    }


def project_baseline(
    discovery_graph: Mapping[str, Any],
    transition_violations: Mapping[str, Any],
    char_path: Mapping[str, Any],
    *,
    source_hashes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Project the closed-world baseline from its three factual authorities."""

    violations = [
        {
            key: item[key]
            for key in ("violation_id", "source_module", "destination_module", "edge_kind", "target_rule_id", "assigned_lane")
        }
        for item in transition_violations["violations"]
    ]
    violations.sort(key=lambda item: str(item["violation_id"]))
    return {
        "artifact": "w21-architecture-baseline-projection",
        "schema_version": 1,
        "baseline": dict(discovery_graph["baseline"]),
        "authority_sources": dict(source_hashes or _BASELINE_SOURCE_HASHES),
        "graph_signatures": _graph_signatures(discovery_graph),
        "baseline_cross_package_pair_count": len(_baseline_pairs(discovery_graph)),
        "baseline_cross_package_module_pairs": _baseline_pairs(discovery_graph),
        "frozen_transition_violation_count": len(violations),
        "frozen_transition_violations": violations,
        "legacy_path_surface": _legacy_surface(char_path),
    }
