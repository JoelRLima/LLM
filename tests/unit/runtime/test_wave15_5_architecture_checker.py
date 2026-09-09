from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.check_wave15_5_architecture import (
    CORRECTIVE_MUTATION_ARMS,
    REQUIRED_MUTATION_ARMS,
    ROOT,
    check_architecture,
    run_corrective_mutation_campaign,
    run_mutation_campaign,
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_wave15_5_architecture_clean_candidate_passes() -> None:
    assert check_architecture(ROOT) == []


def test_wave15_5_mutation_campaign_catches_every_required_arm_without_source_edits() -> None:
    watched = (
        ROOT / "agent/observability/audit_projection.py",
        ROOT / "agent/application_result.py",
        ROOT / "scripts/check_wave15_5_architecture.py",
    )
    before = {path: _digest(path) for path in watched}
    campaign = run_mutation_campaign(ROOT)
    assert campaign["total"] == 16
    assert campaign["detected"] == 16
    assert campaign["failed"] == 0
    assert tuple(item["arm_id"] for item in campaign["arms"]) == REQUIRED_MUTATION_ARMS
    assert {path: _digest(path) for path in watched} == before


def test_wave15_5_corrective_mutation_campaign_catches_run_scope_and_provider_http_bypasses() -> None:
    watched = (
        ROOT / "agent/application_result.py",
        ROOT / "agent/llm/providers/openai_compatible.py",
        ROOT / "agent/llm/providers/openai_input_tokens.py",
        ROOT / "scripts/check_wave15_5_architecture.py",
    )
    before = {path: _digest(path) for path in watched}
    campaign = run_corrective_mutation_campaign(ROOT)
    assert campaign["total"] == len(CORRECTIVE_MUTATION_ARMS)
    assert campaign["detected"] == len(CORRECTIVE_MUTATION_ARMS)
    assert campaign["failed"] == 0
    assert tuple(item["arm_id"] for item in campaign["arms"]) == CORRECTIVE_MUTATION_ARMS
    assert {path: _digest(path) for path in watched} == before
