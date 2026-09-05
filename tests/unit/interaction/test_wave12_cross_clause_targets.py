from __future__ import annotations

from agent.interaction.guards import CrossClauseEffectConflictGuard, CrossClauseRelation


def test_cross_clause_independence_requires_disjoint_normalized_paths() -> None:
    assert CrossClauseEffectConflictGuard.classify(
        "Do not alter README.md. Delete parser.py."
    ) is CrossClauseRelation.INDEPENDENT
    assert CrossClauseEffectConflictGuard.classify(
        "Do not touch that file. Delete parser.py."
    ) is CrossClauseRelation.UNKNOWN_RELATION_CONFLICT
