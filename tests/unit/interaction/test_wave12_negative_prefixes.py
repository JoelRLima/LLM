from __future__ import annotations

from agent.interaction.guards import CrossClauseEffectConflictGuard, CrossClauseRelation


def test_one_exact_polite_prefix_is_consumed_by_negative_scanner() -> None:
    assert CrossClauseEffectConflictGuard.classify(
        "Please, do not delete parser.py. Delete parser.py."
    ) is CrossClauseRelation.SAME_TARGET_CONFLICT
    assert CrossClauseEffectConflictGuard.classify(
        "Por favor, não altere parser.py. Aplique o patch em parser.py."
    ) is CrossClauseRelation.SAME_TARGET_CONFLICT
