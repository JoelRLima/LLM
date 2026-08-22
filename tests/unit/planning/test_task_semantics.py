from types import SimpleNamespace

import pytest

from agent.planning.task_completion import initialize_task_progression
from agent.planning.task_semantics import (
    ObligationStatus,
    TaskIntent,
    TaskObligation,
    TaskSemantics,
    TaskSemanticsError,
    infer_effect_semantics,
)
from agent.state import AgentState


def test_effect_semantics_preserves_requested_and_prohibited_without_direct_text_effect() -> None:
    mixed = infer_effect_semantics(
        "Se X for verdadeiro, escreva Y; caso contrario, nao altere nada."
    )
    assert mixed.requested == ("write",)
    assert mixed.prohibited == ("write",)

    direct = infer_effect_semantics("Escreva exatamente o texto abaixo.")
    assert direct.requested == ()
    assert direct.prohibited == ()


def test_incomplete_h2_plan_keeps_search_obligation_pending() -> None:
    state = AgentState()
    initialize_task_progression(
        SimpleNamespace(agent_state=state),
        "Leia fonte_h2.txt e depois procure nos outros arquivos pela palavra que ele contem.",
    )

    state.record_tool_result(
        "file_reader",
        {"file_path": "fonte_h2.txt"},
        {"ok": True, "done": True, "status": "succeeded", "data": "orion"},
    )
    assert [item.kind for item in state.pending_obligations()] == ["search"]

    state.record_tool_result(
        "grep",
        {"path": ".", "pattern": "orion"},
        {"ok": True, "done": True, "status": "succeeded", "data": []},
    )
    assert state.pending_obligations() == ()


def test_obligation_review_is_bounded_unique_and_not_model_terminal_authority() -> None:
    state = AgentState()
    initialize_task_progression(SimpleNamespace(agent_state=state), "explique o resultado")
    before = state.task_obligations

    with pytest.raises(TaskSemanticsError):
        state.review_task_obligations(
            [{"id": "new", "kind": "custom", "description": "x", "status": "satisfied"}],
            source="initial_plan",
        )
    with pytest.raises(TaskSemanticsError):
        state.review_task_obligations(
            [{"id": "new", "kind": "custom", "description": "x"}],
            source="tool_output",
        )
    assert state.task_obligations == before

    with pytest.raises(TaskSemanticsError):
        state.review_task_obligations(
            [
                {"id": "same", "kind": "custom", "description": "a"},
                {"id": "same", "kind": "custom", "description": "b"},
            ],
            source="canonical_review",
        )
    assert state.task_obligations == before


def test_obligation_transitions_require_evidence_and_checkpoint_round_trip() -> None:
    semantics = TaskSemantics(
        TaskIntent("objetivo"),
        [TaskObligation("read", "read", "ler a fonte")],
    )
    with pytest.raises(TaskSemanticsError):
        semantics.satisfy("read", evidence_ref=None)  # type: ignore[arg-type]
    semantics.satisfy("read", evidence_ref=1)
    assert semantics.obligation_status("read") is ObligationStatus.SATISFIED
    restored = TaskSemantics.from_checkpoint_dict(semantics.to_checkpoint_dict())
    assert restored.obligation_status("read") is ObligationStatus.SATISFIED
    assert restored.obligation_evidence("read") == (1,)


def test_unrequested_observed_effect_is_evidence_but_not_a_new_request() -> None:
    state = AgentState()
    state.record_executed_effect("write", evidence_ref=1)
    assert state.requested_effects == []
    assert state.executed_effects == ["write"]
    assert state.pending_effects() == ()
