import pytest

from agent.evaluation.comparison import EvaluationComparisonError, compare_receipt_groups


def test_empty_receipt_comparison_fails_closed():
    with pytest.raises(EvaluationComparisonError):
        compare_receipt_groups([])


def test_comparison_rejects_conflicting_candidate_identity_metadata():
    with pytest.raises(ValueError):
        compare_receipt_groups(
            {
                "current": [
                    {"candidate_identity": "candidate-a", "receipt_id": "invalid"},
                ],
                "reference": [
                    {"candidate_identity": "candidate-b", "receipt_id": "invalid"},
                ],
            }
        )
