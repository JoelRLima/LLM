from __future__ import annotations

import pytest

from llm_agent.agent.interaction.admission import project_guard_result
from llm_agent.agent.interaction.guards import DirectReadRequestGuard, ReadClassification
from llm_agent.agent.interaction.types import InteractionBoundary


@pytest.mark.parametrize(
    "text",
    [
        "Analise parser.py.",
        "Leia README.md e explique.",
        "Compare a.py e b.py.",
        "O que é uma AST?",
    ],
)
def test_closed_read_lexicon_accepts_grounded_information_requests(text: str) -> None:
    assert DirectReadRequestGuard.classify(text) is ReadClassification.DIRECT_READ


@pytest.mark.parametrize("text", ["Revise esse módulo.", "Review this module."])
def test_deictic_read_is_grounding_clarify(text: str) -> None:
    assert DirectReadRequestGuard.classify(text) is ReadClassification.CONTEXTUAL
    result = project_guard_result(
        ReadClassification.CONTEXTUAL,
        boundary=InteractionBoundary.NATURAL,
    )
    assert result.reason_code == "INTERACTION_CONTEXT_GROUNDING_REQUIRED"
