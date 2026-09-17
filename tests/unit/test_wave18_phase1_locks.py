"""Focused tests for the frozen Phase-0 lock material used by Phase 1."""

from __future__ import annotations

from pathlib import Path

from distribution.lockfiles import validate_bootstrap_lock, validate_runtime_lock

ROOT = Path(__file__).resolve().parents[2]


def test_bootstrap_and_runtime_locks_are_exact_and_binary_only() -> None:
    bootstrap = validate_bootstrap_lock(ROOT / "distribution" / "bootstrap-pip.lock")
    runtime = validate_runtime_lock(ROOT / "distribution" / "runtime-windows-py312.lock")

    assert bootstrap.packages == ("pip",)
    assert bootstrap.hash_count == 1
    assert bootstrap.sdist_count == 0
    assert bootstrap.binary_only is True
    assert len(runtime.packages) == 15
    assert runtime.hash_count == 403
    assert runtime.sdist_count == 0
    assert runtime.binary_only is True
