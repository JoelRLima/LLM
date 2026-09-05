from __future__ import annotations

import pytest

from agent.interaction.guards import CrossClauseEffectConflictGuard, CrossClauseRelation, parse_negative_restriction


def test_family_all_restriction_conflicts_without_extra_target() -> None:
    restriction = parse_negative_restriction("Do not use the network")
    assert restriction is not None
    assert restriction.scope == "FAMILY_ALL"
    assert CrossClauseEffectConflictGuard.classify("Do not use the network. Search the web for notes") is CrossClauseRelation.FAMILY_CONFLICT


@pytest.mark.parametrize(
    "clause",
    [
        "sem pesquisar na web",
        "sem buscar na web",
        "without searching the web",
        "without browsing the web",
    ],
)
def test_network_family_all_negative_infinitives_accept_exact_core(clause: str) -> None:
    restriction = parse_negative_restriction(clause)
    assert restriction is not None
    assert restriction.family == "NETWORK"
    assert restriction.scope == "FAMILY_ALL"
