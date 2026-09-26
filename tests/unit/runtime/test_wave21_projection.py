"""Tests for deterministic committed W21 semantic projections."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.w21_architecture.projection import project_compatibility, project_policy

ROOT = Path(__file__).resolve().parents[3]


def _json(relative: str) -> dict[str, Any]:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in _strings(child)]
    return []


def test_committed_baseline_has_the_frozen_graph_and_transition_shape() -> None:
    baseline = _json("quality/architecture_baseline.json")
    assert baseline["baseline_cross_package_pair_count"] == 1297
    assert len(baseline["baseline_cross_package_module_pairs"]) == 1297
    assert baseline["frozen_transition_violation_count"] == 27
    assert len(baseline["frozen_transition_violations"]) == 27
    assert baseline["legacy_path_surface"]["baseline_active_consumer_count"] == 9
    assert baseline["graph_signatures"] == {
        "production_module_count": 806,
        "module_identity_sha256": "39a9075b59d7e7f3b99ff1cad480874f22814f92d06d2493a511882507480614",
        "static_module_edge_count": 2958,
        "static_module_edge_semantic_sha256": "6a22c5d709491218ee42486840f802758d35d8bdfeb60ccb0baceee800da842d",
        "architecture_union_edge_count": 2982,
        "architecture_union_edge_semantic_sha256": "aa41b901bf900cff8b6b5610f27b787e37a490783672b74aab8896b4506a9b29",
        "static_package_edge_count": 325,
        "architecture_union_package_edge_count": 327,
        "static_nontrivial_scc_count": 33,
        "architecture_union_nontrivial_scc_count": 32,
    }


def test_committed_projections_are_machine_independent() -> None:
    policy = _json("quality/architecture_policy.json")
    compatibility = _json("quality/architecture_compatibility.json")
    baseline = _json("quality/architecture_baseline.json")
    assert project_policy(policy) == policy
    assert project_compatibility(compatibility) == compatibility
    assert all(":\\" not in value for value in _strings(baseline))
    assert all(":\\" not in value for value in _strings(policy))
    assert all(":\\" not in value for value in _strings(compatibility))
