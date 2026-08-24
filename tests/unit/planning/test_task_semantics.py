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
        {
            "ok": True,
            "done": True,
            "status": "succeeded",
            "complete": True,
            "data": "orion",
        },
    )
    assert [item.kind for item in state.pending_obligations()] == ["search"]

    state.record_tool_result(
        "grep",
        {"path": ".", "pattern": "orion"},
        {
            "ok": True,
            "done": True,
            "status": "succeeded",
            "complete": True,
            "data": [],
        },
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


def test_equivalent_canonical_review_amendment_is_rejected_as_no_progress() -> None:
    state = AgentState()
    initialize_task_progression(SimpleNamespace(agent_state=state), "explique o resultado")
    state.review_task_obligations(
        [
            {
                "id": "review:first",
                "kind": "read",
                "target": "a.txt",
                "description": "Ler a.txt.",
            }
        ],
        source="canonical_review",
    )
    before = state.task_obligations

    with pytest.raises(TaskSemanticsError):
        state.review_task_obligations(
            [
                {
                    "id": "review:equivalent",
                    "kind": "read",
                    "target": "a.txt",
                    "description": "A mesma leitura com outro id.",
                }
            ],
            source="canonical_review",
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
        [TaskObligation("read", "read", "ler a fonte", target="fonte.txt")],
    )
    with pytest.raises(TaskSemanticsError):
        semantics.satisfy("read", evidence_ref=None)  # type: ignore[arg-type]
    semantics.observe_tool(
        "file_reader",
        {
            "ok": True,
            "done": True,
            "status": "succeeded",
            "complete": True,
            "data": "conteudo",
        },
        evidence_ref=1,
        args={"file_path": "fonte.txt"},
    )
    assert semantics.obligation_status("read") is ObligationStatus.SATISFIED
    restored = TaskSemantics.from_checkpoint_dict(semantics.to_checkpoint_dict())
    assert restored.obligation_status("read") is ObligationStatus.PENDING
    assert restored.obligation_evidence("read") == ()
    assert restored.terminal_evidence_complete() is False
    assert restored.to_checkpoint_dict()["statuses"]["read"] == "satisfied"


def test_unrequested_effect_requires_canonical_authority() -> None:
    state = AgentState()
    with pytest.raises(TaskSemanticsError):
        state.record_executed_effect("write", evidence_ref=1)
    assert state.requested_effects == []
    assert state.executed_effects == []
    assert state.pending_effects() == ()


def _complete(data):
    return {
        "ok": True,
        "done": True,
        "executed": True,
        "status": "succeeded",
        "complete": True,
        "data": data,
    }


def test_read_evidence_is_bound_to_the_requested_target() -> None:
    semantics = TaskSemantics.from_objective("Leia a.txt e b.txt.")

    semantics.observe_tool(
        "file_reader", _complete("A"), evidence_ref=1, args={"file_path": "a.txt"}
    )

    assert semantics.obligation_status("read:1") is ObligationStatus.SATISFIED
    assert semantics.obligation_status("read:2") is ObligationStatus.PENDING

    semantics.observe_tool(
        "file_reader", _complete("B"), evidence_ref=2, args={"file_path": "b.txt"}
    )
    assert semantics.obligation_status("read:2") is ObligationStatus.SATISFIED


def test_search_evidence_requires_the_exact_query() -> None:
    semantics = TaskSemantics(
        TaskIntent("procure X"),
        [TaskObligation("search-x", "search", "buscar X", query="X")],
        _strict_evidence=True,
    )

    assert semantics.observe_tool(
        "grep", _complete([]), evidence_ref=1, args={"path": ".", "pattern": "Y"}
    ) == ()
    assert semantics.obligation_status("search-x") is ObligationStatus.PENDING

    semantics.observe_tool(
        "grep", _complete([]), evidence_ref=2, args={"path": ".", "pattern": "X"}
    )
    assert semantics.obligation_status("search-x") is ObligationStatus.SATISFIED


def test_d4_zero_match_search_is_successful_negative_evidence() -> None:
    semantics = TaskSemantics(
        TaskIntent("procure X"),
        [TaskObligation("search-x", "search", "buscar X", query="X")],
        _strict_evidence=True,
    )

    semantics.observe_tool(
        "grep",
        {
            "ok": True,
            "done": True,
            "executed": True,
            "status": "succeeded",
            "complete": True,
            "data": [],
            "total_matches": 0,
        },
        evidence_ref=1,
        args={"path": ".", "pattern": "X"},
    )

    assert semantics.obligation_status("search-x") is ObligationStatus.SATISFIED
    assert semantics.obligation_evidence("search-x") == (1,)


def test_explicit_search_literal_precedes_later_observed_value_language() -> None:
    semantics = TaskSemantics.from_objective(
        "H3: encontre H3_SOURCE_MARKER e use o texto observado para buscar a ocorrencia correspondente."
    )

    obligation = semantics.obligations[0]
    assert obligation.kind == "search"
    assert obligation.query == "h3_source_marker"
    assert obligation.query_source is None


def test_generic_search_language_does_not_create_unprovable_obligation() -> None:
    semantics = TaskSemantics.from_objective(
        "Busque a evidência no workspace e informe o resultado."
    )

    assert all(item.kind != "search" for item in semantics.obligations)


def test_explicit_truncated_search_request_accepts_truncation_evidence() -> None:
    semantics = TaskSemantics.from_objective(
        "Busque H9_TRUNCATED_SENTINEL, limite a observacao e informe se ela foi truncada."
    )

    semantics.observe_tool(
        "grep",
        {
            "ok": True,
            "done": True,
            "executed": True,
            "status": "succeeded",
            "data": [{"file": "one.txt"}],
            "artifacts": [{"metadata": {"complete": False, "truncated": True}}],
        },
        evidence_ref=1,
        args={"path": ".", "pattern": "H9_TRUNCATED_SENTINEL"},
    )

    assert semantics.obligation_status("requirement:search") is ObligationStatus.SATISFIED
    assert semantics.obligation_evidence("requirement:search") == (1,)


def test_compare_requires_both_complete_reads_and_accepts_empty_values() -> None:
    semantics = TaskSemantics.from_objective(
        "Compare a.txt e b.txt e diga se o conteudo e igual."
    )

    semantics.observe_tool(
        "file_reader", _complete(""), evidence_ref=1, args={"file_path": "a.txt"}
    )
    assert semantics.obligation_status("requirement:compare") is ObligationStatus.PENDING

    semantics.observe_tool(
        "file_reader", _complete(""), evidence_ref=2, args={"file_path": "b.txt"}
    )
    assert semantics.obligation_status("requirement:compare") is ObligationStatus.SATISFIED
    assert semantics.obligation_evidence("requirement:compare") == (1, 2)


def test_model_obligation_forms_and_evidence_provenance_fail_closed() -> None:
    state = AgentState()
    initialize_task_progression(SimpleNamespace(agent_state=state), "Leia a.txt e b.txt.")

    with pytest.raises(TaskSemanticsError):
        state.review_task_obligations(
            [{"id": "unsupported", "kind": "report", "description": "gerar relatorio"}],
            source="initial_plan",
        )

    state.record_tool_result("file_reader", {"file_path": "a.txt"}, _complete("A"))
    with pytest.raises(TaskSemanticsError):
        state.satisfy_obligation("read:2", evidence_ref=1)


def test_structured_obligation_checkpoint_round_trip_is_exact() -> None:
    semantics = TaskSemantics.from_objective("Compare a.txt e b.txt.")
    semantics.observe_tool(
        "file_reader", _complete(""), evidence_ref=1, args={"file_path": "a.txt"}
    )
    semantics.observe_tool(
        "file_reader", _complete(""), evidence_ref=2, args={"file_path": "b.txt"}
    )

    restored = TaskSemantics.from_checkpoint_dict(semantics.to_checkpoint_dict())
    comparison = next(item for item in restored.obligations if item.kind == "compare")
    assert comparison.operands == ("a.txt", "b.txt")
    assert restored.obligation_status(comparison.id) is ObligationStatus.PENDING
    assert restored.obligation_evidence(comparison.id) == ()
    assert restored.terminal_evidence_complete() is False


def test_checkpoint_without_closed_semantics_version_fails_closed() -> None:
    semantics = TaskSemantics.from_objective("Leia a.txt.")
    checkpoint = semantics.to_checkpoint_dict()
    checkpoint.pop("schema_version")

    with pytest.raises(TaskSemanticsError):
        TaskSemantics.from_checkpoint_dict(checkpoint)
