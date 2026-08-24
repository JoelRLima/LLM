import copy
from types import SimpleNamespace

import pytest

from agent.planning.plan_builder import PlanBuilder
from agent.planning.task_completion import (
    allow_linear_completion,
    continue_after_reasoning_boundary,
    initialize_task_progression,
)
from agent.planning.task_semantics import AdmissionSource, TaskSemantics, TaskSemanticsError
from agent.planning.task_semantics_checkpoint import _admission_digest
from agent.state import AgentState


def _complete(data: object) -> dict[str, object]:
    return {
        "ok": True,
        "done": True,
        "executed": True,
        "status": "succeeded",
        "complete": True,
        "data": data,
    }


def _local_failure() -> dict[str, object]:
    return {
        "ok": False,
        "done": True,
        "executed": True,
        "status": "failed",
        "error": "arquivo nao encontrado",
        "error_code": "FILE_NOT_FOUND",
    }


def _exact_source(data: object, source: str) -> dict[str, object]:
    result = _complete(data)
    result.update(
        {
            "evidence_provenance": "EXACT_SOURCE",
            "source_identity": source,
            "source_hash": "test-source-hash",
            "source_extent": {"kind": "whole"},
        }
    )
    return result


def _recompute_admission_integrity(semantics: dict[str, object]) -> None:
    obligations = semantics["obligations"]
    integrity = semantics["admission_integrity"]
    assert isinstance(obligations, list) and isinstance(integrity, dict)
    for obligation in obligations:
        assert isinstance(obligation, dict)
        integrity[obligation["id"]] = _admission_digest(obligation)


def _runtime(state: AgentState) -> SimpleNamespace:
    return SimpleNamespace(
        agent_state=state,
        tool_registry=None,
        _task_failed=False,
        _cancelled=False,
        _emit=lambda *_args, **_kwargs: None,
    )


def test_model_unrelated_read_is_reported_and_not_durable() -> None:
    semantics = TaskSemantics.empty("Explique a situacao.")
    before = semantics.to_checkpoint_dict()

    report = semantics.review_obligations(
        [
            {
                "id": "invented-read",
                "kind": "read",
                "target": "unrelated.txt",
                "description": "Ler um arquivo nao solicitado.",
            }
        ],
        source="initial_plan",
    )

    assert not report.accepted
    assert report.rejected[0].code == "UNRELATED_OBJECTIVE"
    assert semantics.obligations == ()
    assert semantics.to_checkpoint_dict() == before


def test_model_unrelated_search_is_rejected() -> None:
    semantics = TaskSemantics.empty("Explique a situacao.")

    report = semantics.review_obligations(
        [
            {
                "id": "invented-search",
                "kind": "search",
                "query": "segredo",
                "description": "Buscar uma palavra nao solicitada.",
            }
        ],
        source="initial_plan",
    )

    assert not report.accepted
    assert report.rejected[0].code == "UNRELATED_OBJECTIVE"
    assert semantics.obligations == ()


def test_model_effect_cannot_expand_requested_effect_set() -> None:
    semantics = TaskSemantics.empty("Analise a situacao.")

    report = semantics.review_obligations(
        [
            {
                "id": "invented-effect",
                "kind": "effect",
                "effect": "write",
                "description": "Alterar arquivos sem pedido.",
            }
        ],
        source="initial_plan",
    )

    assert not report.accepted
    assert report.rejected
    assert semantics.requested_effects == ()
    assert semantics.obligations == ()


def test_objective_derived_read_is_admitted_deterministically() -> None:
    semantics = TaskSemantics.empty("Leia a.txt.")

    report = semantics.review_obligations(
        [
            {
                "id": "read-from-objective",
                "kind": "read",
                "target": "a.txt",
                "description": "Ler a.txt conforme o objetivo.",
            }
        ],
        source="initial_plan",
    )

    assert len(report.accepted) == 1
    assert report.accepted[0].admission_source is AdmissionSource.OBJECTIVE_DERIVED
    assert report.accepted[0].admission_evidence_ref is None


def test_fallback_requires_and_records_matching_canonical_failure() -> None:
    semantics = TaskSemantics.empty("Leia missing.txt e explique o motivo se nao puder ser lido.")
    semantics.register_observation(
        "file_reader",
        _local_failure(),
        evidence_ref=7,
        args={"file_path": "missing.txt"},
    )

    report = semantics.review_obligations(
        [
            {
                "id": "fallback:missing",
                "kind": "fallback",
                "fallback_target": "missing.txt",
                "description": "Explicar a falha local de missing.txt.",
            }
        ],
        source="canonical_review",
    )

    assert len(report.accepted) == 1
    admitted = report.accepted[0]
    assert admitted.admission_source is AdmissionSource.CANONICAL_EVIDENCE_DERIVED
    assert admitted.admission_evidence_ref == 7


def test_previous_read_search_requires_causal_canonical_read() -> None:
    objective = "Leia fonte.txt e procure nos outros arquivos pela palavra que ele contem."
    semantics = TaskSemantics.empty(objective)
    proposal = [
        {
            "id": "search:previous",
            "kind": "search",
            "query_source": "previous_read",
            "description": "Procurar o valor da leitura anterior.",
        }
    ]

    rejected = semantics.review_obligations(proposal, source="initial_plan")
    assert rejected.rejected[0].code == "MISSING_CAUSAL_EVIDENCE"

    semantics.register_observation(
        "file_reader",
        _complete("orion"),
        evidence_ref=3,
        args={"file_path": "fonte.txt"},
    )
    accepted = semantics.review_obligations(proposal, source="initial_plan")
    assert len(accepted.accepted) == 1
    assert accepted.accepted[0].admission_source is AdmissionSource.CANONICAL_EVIDENCE_DERIVED
    assert accepted.accepted[0].admission_evidence_ref == 3


def test_safety_required_obligation_has_only_trusted_runtime_path() -> None:
    semantics = TaskSemantics.empty("Explique a situacao.")
    raw = [
        {
            "id": "safety-read",
            "kind": "read",
            "target": "policy.txt",
            "description": "Ler a politica antes de continuar.",
        }
    ]

    rejected = semantics.review_obligations(raw, source="initial_plan")
    assert rejected.rejected
    assert not semantics.obligations

    admitted = semantics.admit_safety_required(raw, reason="policy gate")
    assert admitted[0].admission_source is AdmissionSource.SAFETY_REQUIRED
    assert admitted[0].admission_authorization == "runtime:safety:policy gate"


def test_external_authorization_is_explicit_and_serialized() -> None:
    semantics = TaskSemantics.empty("Explique a situacao.")
    admitted = semantics.admit_externally_authorized(
        [
            {
                "id": "external-read",
                "kind": "read",
                "target": "approved.txt",
                "description": "Ler o arquivo autorizado externamente.",
            }
        ],
        authorization="ticket-42",
    )

    assert admitted[0].admission_source is AdmissionSource.EXTERNALLY_AUTHORIZED
    checkpoint = semantics.to_checkpoint_dict()
    assert checkpoint["obligations"][0]["admission_source"] == "EXTERNALLY_AUTHORIZED"
    assert checkpoint["obligations"][0]["admission_authorization"] == "external:ticket-42"


def test_checkpoint_integrity_rejects_forged_admission_source() -> None:
    semantics = TaskSemantics.empty("Leia a.txt.")
    semantics.review_obligations(
        [
            {
                "id": "read-a",
                "kind": "read",
                "target": "a.txt",
                "description": "Ler a.txt.",
            }
        ],
        source="initial_plan",
    )
    checkpoint = copy.deepcopy(semantics.to_checkpoint_dict())
    checkpoint["obligations"][0]["admission_source"] = "EXTERNALLY_AUTHORIZED"

    with pytest.raises(TaskSemanticsError, match="proveniencia|autorizacao"):
        TaskSemantics.from_checkpoint_dict(checkpoint)


def test_state_restore_rejects_rehashed_objective_derived_target() -> None:
    state = AgentState()
    state.initialize_task_semantics("Leia a.txt.")
    state.task_semantics.review_obligations(
        [
            {
                "id": "read-a",
                "kind": "read",
                "target": "a.txt",
                "description": "Ler a.txt.",
            }
        ],
        source="initial_plan",
    )
    checkpoint = copy.deepcopy(state.to_checkpoint_dict())
    semantics = checkpoint["task_semantics"]
    assert isinstance(semantics, dict)
    obligation = semantics["obligations"][0]
    obligation["target"] = "secret_unrelated.txt"
    semantics["admission_integrity"][obligation["id"]] = _admission_digest(obligation)

    with pytest.raises(ValueError, match="objetivo original"):
        AgentState().from_checkpoint_dict(checkpoint)


def test_legitimate_objective_derived_obligation_restores() -> None:
    state = AgentState()
    state.initialize_task_semantics("Leia a.txt.")
    state.task_semantics.review_obligations(
        [
            {
                "id": "read-a",
                "kind": "read",
                "target": "a.txt",
                "description": "Ler a.txt.",
            }
        ],
        source="initial_plan",
    )

    restored = AgentState()
    restored.from_checkpoint_dict(state.to_checkpoint_dict())

    assert restored.task_obligations[0].target == "a.txt"
    assert restored.task_obligations[0].admission_source is AdmissionSource.OBJECTIVE_DERIVED


def test_legitimate_evidence_derived_fallback_restores_after_history_rebuild() -> None:
    state = AgentState()
    state.initialize_task_semantics(
        "Leia missing.txt e explique o motivo se nao puder ser lido."
    )
    state.record_tool_result(
        "file_reader",
        {"file_path": "missing.txt"},
        {
            "ok": False,
            "done": True,
            "executed": True,
            "status": "failed",
            "error": "arquivo nao encontrado",
            "error_code": "FILE_NOT_FOUND",
            "invocation_id": "missing-1",
        },
    )
    report = state.task_semantics.review_obligations(
        [
            {
                "id": "fallback-missing",
                "kind": "fallback",
                "fallback_target": "missing.txt",
                "description": "Explicar a falha local de missing.txt.",
            }
        ],
        source="initial_plan",
    )
    assert report.accepted[0].admission_source is AdmissionSource.CANONICAL_EVIDENCE_DERIVED

    restored = AgentState()
    restored.from_checkpoint_dict(state.to_checkpoint_dict())

    fallback = next(item for item in restored.task_obligations if item.id == "fallback-missing")
    assert fallback.admission_source is AdmissionSource.CANONICAL_EVIDENCE_DERIVED
    assert fallback.admission_evidence_ref == 1


def test_rehashed_fallback_without_required_read_is_rejected() -> None:
    state = AgentState()
    state.initialize_task_semantics(
        "Leia missing.txt e explique o motivo se nao puder ser lido."
    )
    state.record_tool_result(
        "file_reader",
        {"file_path": "missing.txt"},
        {
            **_local_failure(),
            "invocation_id": "missing-1",
        },
    )
    report = state.task_semantics.review_obligations(
        [
            {
                "id": "fallback-missing",
                "kind": "fallback",
                "fallback_target": "missing.txt",
                "description": "Explicar a falha local de missing.txt.",
            }
        ],
        source="initial_plan",
    )
    assert report.accepted
    checkpoint = copy.deepcopy(state.to_checkpoint_dict())
    checkpoint["objective"] = "Leia unrelated.txt."
    semantics = checkpoint["task_semantics"]
    assert isinstance(semantics, dict)
    semantics["objective"] = "Leia unrelated.txt."
    obligations = semantics["obligations"]
    statuses = semantics["statuses"]
    assert isinstance(obligations, list) and isinstance(statuses, dict)
    for obligation in obligations:
        assert isinstance(obligation, dict)
        if obligation["kind"] == "read":
            obligation["target"] = "unrelated.txt"
        statuses[obligation["id"]] = "pending"
    semantics["evidence"] = {}
    _recompute_admission_integrity(semantics)

    with pytest.raises(ValueError, match="read requerido"):
        AgentState().from_checkpoint_dict(checkpoint)


def test_rehashed_previous_read_without_objective_entailment_is_rejected() -> None:
    objective = "Leia fonte.txt e procure nos outros arquivos pela palavra que ele contem."
    state = AgentState()
    state.objective = objective
    state.set_task_semantics(TaskSemantics.empty(objective))
    state.record_tool_result(
        "file_reader",
        {"file_path": "fonte.txt"},
        {
            **_exact_source("orion", "fonte.txt"),
            "invocation_id": "source-1",
        },
    )
    report = state.task_semantics.review_obligations(
        [
            {
                "id": "search-previous",
                "kind": "search",
                "query_source": "previous_read",
                "description": "Procurar o valor da leitura anterior.",
            }
        ],
        source="initial_plan",
    )
    assert report.accepted[0].admission_source is AdmissionSource.CANONICAL_EVIDENCE_DERIVED
    checkpoint = copy.deepcopy(state.to_checkpoint_dict())

    restored = AgentState()
    restored.from_checkpoint_dict(checkpoint)
    assert restored.task_obligations[0].admission_evidence_ref == 1

    checkpoint["objective"] = "Leia fonte.txt."
    semantics = checkpoint["task_semantics"]
    assert isinstance(semantics, dict)
    semantics["objective"] = "Leia fonte.txt."
    _recompute_admission_integrity(semantics)

    with pytest.raises(ValueError, match="objetivo original"):
        AgentState().from_checkpoint_dict(checkpoint)


@pytest.mark.parametrize("source", (AdmissionSource.SAFETY_REQUIRED, AdmissionSource.EXTERNALLY_AUTHORIZED))
def test_trusted_admission_restore_requires_live_authority_and_accepts_reauthorization(
    source: AdmissionSource,
) -> None:
    state = AgentState()
    state.initialize_task_semantics("Explique a situacao.")
    raw = [
        {
            "id": f"trusted-{source.value.casefold()}",
            "kind": "read",
            "target": "policy.txt",
            "description": "Ler o arquivo autorizado.",
        }
    ]
    if source is AdmissionSource.SAFETY_REQUIRED:
        state.admit_safety_required(raw, reason="policy")
    else:
        state.admit_externally_authorized(raw, authorization="ticket-42")
    checkpoint = state.to_checkpoint_dict()

    with pytest.raises(ValueError, match="autoridade viva|autoridade runtime viva"):
        AgentState().from_checkpoint_dict(checkpoint)

    def live_authority(observed_source: AdmissionSource, obligation: object) -> bool:
        return observed_source is source and getattr(obligation, "target", None) == "policy.txt"

    restored = AgentState()
    restored.from_checkpoint_dict(checkpoint, admission_authority=live_authority)

    assert restored.task_obligations[0].admission_source is source


def test_rejected_model_obligation_is_not_a_hidden_completion_blocker() -> None:
    state = AgentState()
    initialize_task_progression(SimpleNamespace(agent_state=state), "Explique a situacao.")
    report = state.review_task_obligations_report(
        [
            {
                "id": "hidden-read",
                "kind": "read",
                "target": "unrelated.txt",
                "description": "Ler algo que nao foi pedido.",
            }
        ],
        source="canonical_review",
    )

    assert report.rejected
    assert state.task_obligations == ()
    assert allow_linear_completion(_runtime(state), "Explique a situacao.") is None


def test_production_canonical_review_ignores_unrelated_model_requirement() -> None:
    state = AgentState()
    objective = "Explique a situacao."
    initialize_task_progression(SimpleNamespace(agent_state=state), objective)
    state.record_tool_result(
        "echo",
        {},
        {"ok": True, "done": True, "executed": True, "status": "succeeded", "data": "observado"},
    )
    events: list[tuple[str, object]] = []

    class _Context:
        @staticmethod
        def ask_model(*_args, **_kwargs):
            return {
                "action": "complete",
                "reason": "parece suficiente",
                "obligations": [
                    {
                        "id": "review:read",
                        "kind": "read",
                        "target": "a.txt",
                        "description": "Ler a.txt antes de concluir.",
                    }
                ],
            }

    orchestrator = SimpleNamespace(
        agent_state=state,
        context_manager=_Context(),
        plan_builder=None,
        session=SimpleNamespace(config={"max_reasoning_turns": 2}),
        final_responder=None,
        verbose=False,
        _task_failed=False,
        _cancelled=False,
        _emit=lambda event, data=None: events.append((event, data)),
        _log_metric=lambda *_args, **_kwargs: None,
        _build_tools_description=lambda **_kwargs: "echo(...); file_reader(...)",
    )
    orchestrator.plan_builder = PlanBuilder(orchestrator)

    result = continue_after_reasoning_boundary(orchestrator, objective)

    assert result.completed is True
    assert state.task_obligations == ()
    assert ("canonical_review_amendment", {"added": 0}) in events
