from __future__ import annotations

from agent.interaction.guards import MixedIntentClassification, MixedIntentTailGuard


def test_read_and_plan_tails_with_effects_are_mixed() -> None:
    assert MixedIntentTailGuard.classify("Read parser.py and delete obsolete.py") is MixedIntentClassification.MIXED_EFFECT
    assert MixedIntentTailGuard.classify("Propose a plan for parser.py and apply the patch") is MixedIntentClassification.MIXED_EFFECT


def test_mixed_intent_scans_later_tail_marker_occurrences() -> None:
    assert MixedIntentTailGuard.classify(
        "Read parser.py and explain and delete obsolete.py"
    ) is MixedIntentClassification.MIXED_EFFECT
    assert MixedIntentTailGuard.classify(
        "Propose a plan for parser.py and explain and apply the patch"
    ) is MixedIntentClassification.MIXED_EFFECT
