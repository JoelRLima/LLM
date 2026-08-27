from pathlib import Path

import pytest

from agent.planning.provenance_validation import _complete_successful_result
from agent.planning.result_binding_values import result_is_bindable
from agent.reporting.observation_evidence import result_is_successful
from agent.runtime.outcome_taxonomy import OperationalStatus
from agent.tools.result_completeness import (
    canonical_result_successful,
    legacy_result_successful,
)


def _contains_direct_writer(source: str, forbidden: str) -> bool:
    return forbidden in source


@pytest.mark.parametrize(
    "status",
    ("complete", "completed", "success", None, "unknown", "failed", "blocked"),
)
def test_strict_result_success_rejects_noncanonical_status(status: object) -> None:
    assert canonical_result_successful({"ok": True, "status": status}) is False


def test_strict_result_success_accepts_exact_string_and_typed_status() -> None:
    assert canonical_result_successful({"ok": True, "status": "succeeded"}) is True
    assert canonical_result_successful(
        {"ok": True, "status": OperationalStatus.SUCCEEDED}
    ) is True


def test_legacy_alias_adaptation_is_explicit_and_not_causal_evidence() -> None:
    legacy = {
        "ok": True,
        "status": "complete",
        "executed": True,
        "data": "value",
        "complete": True,
    }

    assert legacy_result_successful(legacy) is True
    assert result_is_bindable(legacy) is False
    assert _complete_successful_result(legacy) is False
    assert result_is_successful(legacy) is False


def test_strict_causal_consumers_do_not_import_legacy_adapter() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    relative_paths = (
        "agent/orchestration/security_service.py",
        "agent/orchestration/task_lifecycle.py",
        "agent/planning/provenance_validation.py",
        "agent/planning/result_binding_values.py",
        "agent/planning/task_semantics_evidence.py",
        "agent/reporting/observation_evidence.py",
    )
    visited = []
    for relative_path in relative_paths:
        source_path = repo_root / relative_path
        assert source_path.is_file()
        source = source_path.read_text(encoding="utf-8")
        assert "canonical_result_successful" in source
        assert "legacy_result_successful" not in source
        visited.append(relative_path)
    assert len(visited) == len(relative_paths) > 0


def test_core_writer_modules_have_no_direct_compatibility_fallbacks() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    forbidden = {
        "agent/planning/reactive_loop.py": "state.plan_step = value",
        "agent/orchestration/task_lifecycle.py": "state.last_result = None",
        "agent/runtime/task_execution_context.py": "self.agent_state.last_result = None",
        "agent/planning/observation_invalidation.py": "values.pop(key, None)",
    }
    scanned = 0
    for relative_path, violation in forbidden.items():
        source_path = repo_root / relative_path
        assert source_path.is_file()
        source = source_path.read_text(encoding="utf-8")
        assert _contains_direct_writer(source, violation) is False
        assert _contains_direct_writer(f"seed:{violation}", violation) is True
        scanned += 1
    assert scanned == len(forbidden) > 0
