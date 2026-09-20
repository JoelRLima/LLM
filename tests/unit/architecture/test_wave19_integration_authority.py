from __future__ import annotations

from pathlib import Path

from scripts import check_wave19_architecture as checker

ROOT = Path(__file__).resolve().parents[3]


def test_s08_integration_authority_has_no_boundary_violations() -> None:
    assert checker._check_integration_boundaries(ROOT) == []


def test_s08_full_architecture_gate_remains_green() -> None:
    assert checker.check_architecture(ROOT) == []
