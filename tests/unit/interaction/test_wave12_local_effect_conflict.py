from __future__ import annotations

import pytest

from agent.interaction.admission import admit_interaction
from agent.interaction.guards import LocalConflictClassification, LocalEffectConflictGuard
from agent.interaction.types import (
    ActionGrounding,
    InteractionAction,
    InteractionAmbiguity,
    InteractionBoundary,
    InteractionModelDecision,
)
from agent.runtime.task_directives import TaskDirective


def test_same_segment_contradiction_is_not_silently_downgraded() -> None:
    assert LocalEffectConflictGuard.classify("Run tests but don't run tests") is LocalConflictClassification.CONFLICT
    assert LocalEffectConflictGuard.classify("Delete parser.py and don't delete parser.py") is LocalConflictClassification.CONFLICT


@pytest.mark.parametrize(
    "subject",
    [
        "Search the web for X, do not search the web.",
        "Search the web for X and do not run tests and do not search the web.",
        "Remember X, do not remember X.",
        "Remember X and do not run tests and do not remember X.",
        "Search the web for X, never search the web.",
        "Search the web for X, dont search the web.",
        "Search the web for X and never search the web.",
        "Pesquise na web por X, não pesquise na web.",
        "Pesquise na web por X, nao pesquise na web.",
        "Search the web for X, without searching the web.",
        "Search the web for X, without browsing the web.",
        "Pesquise na web por X, sem pesquisar na web.",
        "Pesquise na web por X, sem buscar na web.",
    ],
)
def test_all_same_segment_negative_tails_are_examined(subject: str) -> None:
    assert LocalEffectConflictGuard.classify(subject) is LocalConflictClassification.CONFLICT


@pytest.mark.parametrize(
    "subject",
    [
        "Search the web for X, do not search the web.",
        "Search the web for X and do not run tests and do not search the web.",
        "Remember X, do not remember X.",
        "Remember X and do not run tests and do not remember X.",
        "Search the web for X, never search the web.",
        "Search the web for X, dont search the web.",
        "Search the web for X and never search the web.",
        "Pesquise na web por X, não pesquise na web.",
        "Pesquise na web por X, nao pesquise na web.",
        "Search the web for X, without searching the web.",
        "Search the web for X, without browsing the web.",
        "Pesquise na web por X, sem pesquisar na web.",
        "Pesquise na web por X, sem buscar na web.",
    ],
)
def test_local_conflict_veto_dominates_model_suggested_do(subject: str) -> None:
    result = admit_interaction(
        boundary=InteractionBoundary.NATURAL,
        visible_user_text=subject,
        subject=subject,
        model_decision=InteractionModelDecision(
            action=InteractionAction.RUN,
            directive=TaskDirective.DO,
            ambiguity=InteractionAmbiguity.NONE,
            grounding=ActionGrounding.CURRENT_TURN,
            operation_requested=True,
            proposal_only=False,
            resume_requested=False,
            evidence=subject,
        ),
    )
    assert result.action is InteractionAction.CLARIFY
    assert result.reason_code == "INTERACTION_CONFLICT"
