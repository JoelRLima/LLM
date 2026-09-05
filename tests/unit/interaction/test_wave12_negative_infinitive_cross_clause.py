from __future__ import annotations

from agent.interaction.guards import CrossClauseEffectConflictGuard, CrossClauseRelation


def test_shared_negative_infinitive_forms_are_seen_cross_clause() -> None:
    assert CrossClauseEffectConflictGuard.classify(
        "Sem alterar parser.py. Aplique o patch em parser.py."
    ) is CrossClauseRelation.SAME_TARGET_CONFLICT
    assert CrossClauseEffectConflictGuard.classify(
        "Without deleting parser.py. Delete parser.py."
    ) is CrossClauseRelation.SAME_TARGET_CONFLICT
