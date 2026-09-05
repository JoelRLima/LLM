from __future__ import annotations

from agent.interaction.guards import DirectOperationalRequestGuard, OperationalClassification


def test_conditional_effects_are_hypothetical() -> None:
    assert DirectOperationalRequestGuard.classify("If tests pass, apply the patch") is OperationalClassification.HYPOTHETICAL
    assert DirectOperationalRequestGuard.classify("Apply the patch if tests pass") is OperationalClassification.HYPOTHETICAL
