from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from scripts.w23_architecture.phase0_inventory import (
    build_inventory,
    canonical_bytes,
    strongly_connected,
)


def test_canonical_bytes_are_sorted_compact_utf8_with_lf() -> None:
    assert canonical_bytes({"z": "á", "a": [2, 1]}) == b'{"a":[2,1],"z":"\xc3\xa1"}\n'


def test_strongly_connected_reports_nontrivial_components_and_self_loops() -> None:
    edges = [
        {"source_module": "a", "destination_module": "b"},
        {"source_module": "b", "destination_module": "a"},
        {"source_module": "c", "destination_module": "c"},
        {"source_module": "d", "destination_module": "a"},
    ]
    assert strongly_connected({"a", "b", "c", "d"}, edges) == [["c"], ["a", "b"]]


def test_phase0_inventory_matches_authority_and_exposes_broker_edges(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    historical_root = tmp_path / "phase0-authority"
    subprocess.run(
        ["git", "clone", "--shared", "--no-checkout", "--quiet", str(root), str(historical_root)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(historical_root), "checkout", "--detach", "e61faff9d04ef7718de7764d61b29c7059d77b96"],
        check=True,
        capture_output=True,
        text=True,
    )
    raw, summary = build_inventory(historical_root)

    assert raw["baseline_commit"] == "e61faff9d04ef7718de7764d61b29c7059d77b96"
    assert raw["baseline_tree"] == "5d858db4b3e6b18e60ac1836c0ed878833dd7549"
    assert len(raw["modules"]) == 867
    assert summary["counts"]["agent_modules"] == 742
    assert summary["counts"]["agent_boundary_exports"] == 87
    assert summary["counts"]["agent_boundary_production_consumers"] == 28
    assert any("symbol_registry" in edge["edge_kinds"] for edge in raw["edges"])
    assert summary["raw_graph"]["sha256"] == hashlib.sha256(canonical_bytes(raw)).hexdigest()
