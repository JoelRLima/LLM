from __future__ import annotations

from agent.interaction.guards import DirectOperationalRequestGuard, DirectOperationalTargetGuard, TargetProof


def test_target_proof_rejects_prose_after_an_anchor() -> None:
    analysis = DirectOperationalRequestGuard.analyze("Delete parser.py from the explanation")
    assert DirectOperationalTargetGuard.classify(analysis) is TargetProof.UNPROVEN
