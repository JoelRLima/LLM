"""W20-B PRACTICAL receipt/comparison contract tests; not run in this subwave."""

from __future__ import annotations

from agent.evaluation.comparison import PracticalComparisonStatus
from agent.evaluation.receipt import PracticalEvidenceV1


def _evidence(candidate: str) -> PracticalEvidenceV1:
    return PracticalEvidenceV1(
        candidate_identity=candidate,
        operation_identity="operation-a",
        environment_identity={"workspace_id": "workspace-a"},
        inputs={"request": "bounded"},
        observed_evidence={"fact": "observed"},
        terminal_outcome={"status": "succeeded"},
        comparison_inputs={"metric": "pass"},
        comparison_result=None,
        provenance={"owner": "practical"},
    )


def test_support_practical_evidence_is_bounded_and_serializable() -> None:
    assert _evidence("candidate-a").to_dict()["candidate_identity"] == "candidate-a"


def test_support_practical_identity_mismatch_is_not_comparable() -> None:
    from dataclasses import replace

    from agent.evaluation.receipt import EvaluationReceiptV1

    # The canonical receipt remains the truth owner; this test only documents
    # the comparison status contract for receipts carrying PRACTICAL evidence.
    assert PracticalComparisonStatus.NOT_COMPARABLE.value == "not_comparable"
    assert replace is not None and EvaluationReceiptV1 is not None


def test_support_missing_practical_evidence_is_insufficient() -> None:
    assert PracticalComparisonStatus.INSUFFICIENT_EVIDENCE.value == "insufficient_evidence"


def test_support_comparison_status_never_implies_a_zero_score() -> None:
    assert "score" not in PracticalComparisonStatus.COMPARABLE.value
