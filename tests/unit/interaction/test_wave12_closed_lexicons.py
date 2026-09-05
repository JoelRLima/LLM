from __future__ import annotations

from agent.interaction.guards import DirectOperationalRequestGuard, OperationalClassification


def test_unlisted_effect_synonyms_do_not_become_direct_operations() -> None:
    assert DirectOperationalRequestGuard.classify("Destroy parser.py") is OperationalClassification.UNKNOWN
    assert DirectOperationalRequestGuard.classify("Please manipulate parser.py") is OperationalClassification.UNKNOWN
