from __future__ import annotations

from agent.interaction.guards import CrossClauseEffectConflictGuard, CrossClauseRelation


def test_network_family_all_negative_keeps_trailing_purpose_in_scope() -> None:
    assert CrossClauseEffectConflictGuard.classify(
        "Without searching the web for project X. Search the web for project Y."
    ) is CrossClauseRelation.FAMILY_CONFLICT
    assert CrossClauseEffectConflictGuard.classify(
        "Do not use the network for this request. Search the web for release notes."
    ) is CrossClauseRelation.FAMILY_CONFLICT
