"""Focused W18 candidate lifetime lease tests (portable portions)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.runtime import candidate_lease


def test_object_names_contain_global_sid_and_validated_candidate() -> None:
    candidate = "w18-" + "a" * 32
    assert candidate_lease.candidate_gate_name(candidate, "S-1-5-21-42") == (
        r"Global\W18-candidate-gate-S-1-5-21-42-" + candidate
    )
    assert candidate_lease.candidate_active_name(candidate, "S-1-5-21-42") == (
        r"Global\W18-candidate-active-S-1-5-21-42-" + candidate
    )


@pytest.mark.parametrize("value", ["", "w18-a", "W18-" + "a" * 32, "w18-" + "g" * 32, "w18-" + "a" * 31])
def test_candidate_id_is_validated_before_mutex_name(value: str) -> None:
    assert not candidate_lease.is_valid_candidate_id(value)
    with pytest.raises(ValueError):
        candidate_lease.candidate_gate_name(value, "S-1-5-18")
    with pytest.raises(ValueError):
        candidate_lease.candidate_active_name(value, "S-1-5-18")


def test_installed_candidate_id_is_derived_from_canonical_launcher_layout(tmp_path: Path) -> None:
    candidate = "w18-" + "b" * 32
    launcher = tmp_path / "versions" / candidate / "app" / "launcher.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("", encoding="utf-8")
    assert candidate_lease.candidate_id_from_launcher(launcher) == candidate
    assert candidate_lease.candidate_id_from_launcher(tmp_path / "payload" / "app" / "launcher.py") is None


def test_non_windows_runtime_lease_is_a_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(candidate_lease.os, "name", "posix")
    assert candidate_lease.acquire_runtime_candidate_lease(tmp_path / "launcher.py") is None


def test_mutex_wait_classifies_active_and_abandoned_ownership() -> None:
    assert candidate_lease.interpret_mutex_wait(0) == "acquired"
    assert candidate_lease.interpret_mutex_wait(0x80) == "acquired"  # WAIT_ABANDONED
    assert candidate_lease.interpret_mutex_wait(0x102) == "active"
    assert candidate_lease.interpret_mutex_wait(0xFFFFFFFF) == "ambiguous"
