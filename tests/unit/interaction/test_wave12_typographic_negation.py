from __future__ import annotations

from agent.interaction.guards import CrossClauseEffectConflictGuard, CrossClauseRelation


def test_typographic_apostrophe_remains_plain_negation() -> None:
    assert CrossClauseEffectConflictGuard.classify(
        "Don’t delete parser.py. Delete parser.py."
    ) is CrossClauseRelation.SAME_TARGET_CONFLICT
