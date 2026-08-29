from __future__ import annotations

from scripts.check_production_naming_hygiene import check_repository, check_text


def _codes(findings: list[str]) -> set[str]:
    return {
        part.rstrip(":")
        for finding in findings
        for part in finding.split()
        if part.startswith("PNH-")
    }


def test_adversarial_path_and_source_controls_are_rejected() -> None:
    assert "PNH-PATH" in _codes(check_text("", "agent/evaluation/block7.py"))
    assert "PNH-ID" in _codes(check_text("class Block7EvidenceError(Exception): ...", "agent/evaluation/evidence.py"))
    assert "PNH-STRING" in _codes(check_text('metadata = {"block7": True}', "agent/evaluation/evidence.py"))
    assert "PNH-STRING" in _codes(check_text('VERSION = "B7-HSERIES-V1.5"', "agent/evaluation/contracts.py"))
    assert "PNH-STRING" in _codes(check_text('HELP = "Block 7 acceptance campaign"', "scripts/run.py"))
    assert "PNH-ID" in _codes(check_text("def phase4_audit(): ...", "agent/evaluation/audit.py"))
    assert "PNH-STRING" in _codes(check_text('HELP = "Phase 5"', "scripts/run.py"))


def test_import_and_docstring_controls_are_rejected() -> None:
    imported = check_text("from agent.evaluation.block7 import H_SERIES as legacy", "agent/evaluation/init.py")
    assert "PNH-IMPORT" in _codes(imported)
    docstring = check_text('"""Wave 2 ownership note."""', "agent/runtime/example.py")
    assert "PNH-DOC" in _codes(docstring)


def test_governance_exception_is_exact_and_narrow() -> None:
    assert check_text('"""Wave 1 architecture gate."""', "scripts/check_wave1_architecture.py") == []
    findings = check_text('"""Wave 1 architecture gate."""', "scripts/other_checker.py")
    assert "PNH-DOC" in _codes(findings)


def test_legitimate_domain_terms_remain_allowed() -> None:
    text = """
H1_H19 = tuple(f"H{i}" for i in range(1, 20))
epoch = "REAL-MODEL-EPOCH-2"
phase_angle = 0.0
block_size = 64
gate = "approval boundary"
"""
    assert check_text(text, "agent/evaluation/domain.py") == []


def test_repository_candidate_passes() -> None:
    assert check_repository(".") == []
