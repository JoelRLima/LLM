from __future__ import annotations

from agent.interaction.guards import DirectOperationalRequestGuard, DirectOperationalTargetGuard, TargetProof


def test_effect_target_is_required_for_inferred_do() -> None:
    analysis = DirectOperationalRequestGuard.analyze("Send me a summary of parser.py")
    assert DirectOperationalTargetGuard.classify(analysis) is TargetProof.UNPROVEN
