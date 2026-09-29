from __future__ import annotations

from llm_agent.agent.interaction.admission import project_guard_result
from llm_agent.agent.interaction.guards import OperationalClassification
from llm_agent.agent.interaction.types import InteractionAction, InteractionBoundary


def test_unknown_operational_guard_projects_to_effect_clarify() -> None:
    result = project_guard_result(OperationalClassification.UNKNOWN, boundary=InteractionBoundary.NATURAL)
    assert result.action is InteractionAction.CLARIFY
    assert result.reason_code == "INTERACTION_EFFECT_AMBIGUOUS"
