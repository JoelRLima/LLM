from __future__ import annotations

from agent.interaction.guards import DirectPlanRequestGuard, PlanClassification


def test_plan_admission_has_one_direct_speech_act_owner() -> None:
    assert DirectPlanRequestGuard.classify("Proponha uma correção para parser.py sem aplicar") is PlanClassification.DIRECT_PLAN
    assert DirectPlanRequestGuard.classify("How would you refactor parser.py?") is PlanClassification.DIRECT_PLAN
