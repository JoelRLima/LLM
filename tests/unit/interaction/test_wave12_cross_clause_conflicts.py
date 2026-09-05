from __future__ import annotations

import pytest

from agent.interaction.guards import CrossClauseEffectConflictGuard, CrossClauseRelation


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Do not delete parser.py. Delete parser.py.", CrossClauseRelation.SAME_TARGET_CONFLICT),
        ("Do not delete a/../parser.py. Delete ./parser.py.", CrossClauseRelation.SAME_TARGET_CONFLICT),
        ("Do not alter src\\parser.py. Delete src/parser.py.", CrossClauseRelation.SAME_TARGET_CONFLICT),
        ("Do not alter README.md. Delete parser.py.", CrossClauseRelation.INDEPENDENT),
        ("Do not touch that file. Modify parser.py.", CrossClauseRelation.UNKNOWN_RELATION_CONFLICT),
        ("Do not use the network. Search the web for release notes.", CrossClauseRelation.FAMILY_CONFLICT),
        ("Do not change anything. Delete parser.py.", CrossClauseRelation.GLOBAL_CONFLICT),
        ("Without deleting parser.py. Delete parser.py.", CrossClauseRelation.SAME_TARGET_CONFLICT),
    ],
)
def test_cross_clause_relation_is_conservative(text: str, expected: CrossClauseRelation) -> None:
    assert CrossClauseEffectConflictGuard.classify(text) is expected
