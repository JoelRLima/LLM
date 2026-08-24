from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from agent.planning.completion_observations import eligible_waiver_observations
from agent.planning.task_completion import refresh_executed_effects
from agent.planning.task_semantics import (
    AdmissionSource,
    ObligationStatus,
    TaskIntent,
    TaskObligation,
    TaskSemantics,
    TaskSemanticsError,
)
from agent.state import AgentState


class _Memory:
    def __init__(self) -> None:
        self.state = {}


def _state() -> AgentState:
    return AgentState(memory=_Memory())


def _complete(data: object) -> dict[str, object]:
    return {
        "ok": True,
        "done": True,
        "executed": True,
        "status": "succeeded",
        "complete": True,
        "data": data,
    }


def _complete_source(data: object, source: str) -> dict[str, object]:
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


def _write_result(*, executed: bool = True, status: str = "succeeded") -> dict[str, object]:
    persisted = status == "succeeded"
    return {
        "ok": status == "succeeded",
        "done": True,
        "executed": executed,
        "status": status,
        "data": {
            "artifacts": [
                {
                    "metadata": {
                        "applied": persisted,
                        "mutation_occurred": persisted,
                        "final_state": "applied" if persisted else "blocked",
                    }
                }
            ]
        },
    }


class _Registry:
    def __init__(self, capabilities: dict[str, set[str]]) -> None:
        self.capabilities = capabilities

    def descriptor(self, tool_name: str) -> SimpleNamespace:
        if tool_name not in self.capabilities:
            raise KeyError(tool_name)
        return SimpleNamespace(capabilities=frozenset(self.capabilities[tool_name]))


class _MalformedRegistry:
    def descriptor(self, tool_name: str) -> SimpleNamespace:
        del tool_name
        return SimpleNamespace()


class _BrokenRegistry:
    def descriptor(self, tool_name: str) -> SimpleNamespace:
        del tool_name
        raise RuntimeError("descriptor unavailable")


def _effect_state(
    tool: str,
    result: dict[str, object],
    capabilities: set[str],
) -> tuple[AgentState, SimpleNamespace]:
    state = _state()
    objective = "write"
    state.objective = objective
    state.set_task_semantics(
        TaskSemantics(
            TaskIntent(objective, ("write",)),
            [TaskObligation("effect:write", "effect", "write", effect="write")],
            _strict_evidence=True,
        )
    )
    state.record_tool_result(tool, {}, result)
    authority = SimpleNamespace(
        agent_state=state,
        tool_registry=_Registry({tool: capabilities}),
    )
    return state, authority


def _unbound_write_state(
    result: dict[str, object] | None = None,
    *,
    prohibited: tuple[str, ...] = ("write",),
) -> AgentState:
    state = _state()
    objective = "Observe a canonical unbound write."
    state.objective = objective
    state.set_task_semantics(
        TaskSemantics(
            TaskIntent(objective, (), prohibited),
            [],
            _strict_evidence=True,
        )
    )
    state.record_tool_result(
        "code_task",
        {},
        result if result is not None else _write_result(),
    )
    return state


def _write_authority() -> SimpleNamespace:
    return SimpleNamespace(
        tool_registry=_Registry({"code_task": {"write"}}),
    )


def _legacy_checkpoint(
    *,
    objective: str = "write",
    executed: list[str] | None = None,
    waived: list[str] | None = None,
    history: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "objective": objective,
        "plan": [],
        "step_records": [],
        "task_semantics": None,
        "requested_effects": ["write"],
        "executed_effects": executed or [],
        "waived_effects": waived or [],
        "prohibited_effects": [],
        "tool_history": history or [],
        "events": [],
        "conversation_history": [],
    }


def _previous_read_state() -> AgentState:
    objective = "Leia fonte.txt e procure nos outros arquivos pela palavra que ele contem."
    state = _state()
    state.objective = objective
    state.set_task_semantics(
        TaskSemantics(
            TaskIntent(objective),
            [
                TaskObligation(
                    "search:previous",
                    "search",
                    "Procurar o valor lido anteriormente.",
                    query_source="previous_read",
                    admission_source=AdmissionSource.CANONICAL_EVIDENCE_DERIVED,
                    admission_evidence_ref=1,
                )
            ],
            _strict_evidence=True,
        )
    )
    return state


def _canonical_read_checkpoint() -> dict[str, object]:
    objective = "Leia b.txt."
    state = _state()
    state.objective = objective
    state.set_task_semantics(
        TaskSemantics(
            TaskIntent(objective),
            [
                TaskObligation(
                    "read:b",
                    "read",
                    "Ler b.txt.",
                    target="b.txt",
                )
            ],
            _strict_evidence=True,
        )
    )
    state.record_tool_result(
        "file_reader",
        {"file_path": "b.txt"},
        _complete_source("B", "b.txt"),
    )
    assert state.obligation_status("read:b") is ObligationStatus.SATISFIED
    return state.to_checkpoint_dict()


def test_checkpoint_restore_revalidates_valid_terminal_evidence() -> None:
    checkpoint = _canonical_read_checkpoint()

    restored = _state()
    restored.from_checkpoint_dict(checkpoint)

    assert restored.obligation_status("read:b") is ObligationStatus.SATISFIED
    assert restored.task_semantics.obligation_evidence("read:b") == (1,)
    assert restored.terminal_evidence_complete() is True


def test_checkpoint_restore_rejects_terminal_evidence_ref_missing_from_history() -> None:
    checkpoint = copy.deepcopy(_canonical_read_checkpoint())
    semantics = checkpoint["task_semantics"]
    assert isinstance(semantics, dict)
    evidence = semantics["evidence"]
    assert isinstance(evidence, dict)
    evidence["read:b"] = [999]

    restored = _state()
    with pytest.raises(
        ValueError,
        match="task semantics evidence does not match canonical history",
    ):
        restored.from_checkpoint_dict(checkpoint)


def test_checkpoint_restore_rejects_existing_ref_that_proves_other_subject() -> None:
    checkpoint = copy.deepcopy(_canonical_read_checkpoint())
    history = checkpoint["tool_history"]
    assert isinstance(history, list) and history
    entry = history[0]
    assert isinstance(entry, dict)
    entry["args"] = {"file_path": "a.txt"}

    restored = _state()
    with pytest.raises(
        ValueError,
        match="task semantics evidence does not match canonical history",
    ):
        restored.from_checkpoint_dict(checkpoint)


def test_checkpoint_restore_preserves_exact_local_failure_fallback() -> None:
    objective = "Leia missing.txt; se nao puder, diga claramente qual e por que."
    state = _state()
    state.objective = objective
    state.set_task_semantics(
        TaskSemantics(
            TaskIntent(objective),
            [
                TaskObligation(
                    "read:missing",
                    "read",
                    "Ler missing.txt.",
                    target="missing.txt",
                ),
                TaskObligation(
                    "fallback:missing",
                    "fallback",
                    "Relatar falha local de missing.txt.",
                    fallback_target="missing.txt",
                    admission_source=AdmissionSource.CANONICAL_EVIDENCE_DERIVED,
                    admission_evidence_ref=1,
                ),
            ],
            _strict_evidence=True,
        )
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
        },
    )
    assert state.obligation_status("fallback:missing") is ObligationStatus.SATISFIED
    assert state.obligation_status("read:missing") is ObligationStatus.WAIVED

    restored = _state()
    restored.from_checkpoint_dict(state.to_checkpoint_dict())

    assert restored.obligation_status("fallback:missing") is ObligationStatus.SATISFIED
    assert restored.obligation_status("read:missing") is ObligationStatus.WAIVED
    assert restored.task_semantics.failure_observation_permitted(1) is True
    assert restored.terminal_evidence_complete() is True


def _local_failure_fallback_semantics() -> TaskSemantics:
    semantics = TaskSemantics(
        TaskIntent("Leia missing.txt; se nao puder, relate a falha."),
        [
            TaskObligation(
                "read:missing",
                "read",
                "Ler missing.txt.",
                target="missing.txt",
            ),
            TaskObligation(
                "fallback:missing",
                "fallback",
                "Relatar falha local de missing.txt.",
                fallback_target="missing.txt",
                admission_source=AdmissionSource.CANONICAL_EVIDENCE_DERIVED,
                admission_evidence_ref=1,
            ),
        ],
        _strict_evidence=True,
    )
    semantics.register_observation(
        "file_reader",
        {
            "ok": False,
            "done": True,
            "executed": True,
            "status": "failed",
            "error": "arquivo nao encontrado",
        },
        evidence_ref=1,
        args={"file_path": "missing.txt"},
    )
    return semantics


def _local_failure_read_only_semantics(target: str = "missing.txt") -> TaskSemantics:
    semantics = TaskSemantics(
        TaskIntent(f"Leia {target}."),
        [
            TaskObligation(
                f"read:{target}",
                "read",
                f"Ler {target}.",
                target=target,
            )
        ],
        _strict_evidence=True,
    )
    semantics.register_observation(
        "file_reader",
        {
            "ok": False,
            "done": True,
            "executed": True,
            "status": "failed",
            "error": "arquivo nao encontrado",
        },
        evidence_ref=1,
        args={"file_path": target},
    )
    return semantics


@pytest.mark.parametrize("method", ("waive", "block"))
def test_successful_read_cannot_prove_waived_or_blocked(method: str) -> None:
    semantics = TaskSemantics(
        TaskIntent("Leia a.txt."),
        [TaskObligation("read:a", "read", "Ler a.txt.", target="a.txt")],
        _strict_evidence=True,
    )
    semantics.register_observation(
        "file_reader",
        _complete("A"),
        evidence_ref=1,
        args={"file_path": "a.txt"},
    )

    with pytest.raises(TaskSemanticsError):
        getattr(semantics, method)("read:a", evidence_ref=1)

    semantics.satisfy("read:a", evidence_ref=1)
    assert semantics.obligation_status("read:a") is ObligationStatus.SATISFIED


def test_recovered_local_read_failure_is_waived_only() -> None:
    semantics = _local_failure_fallback_semantics()
    semantics.satisfy("fallback:missing", evidence_ref=1)

    with pytest.raises(TaskSemanticsError):
        semantics.satisfy("read:missing", evidence_ref=1)
    with pytest.raises(TaskSemanticsError):
        semantics.block("read:missing", evidence_ref=1)

    semantics.waive("read:missing", evidence_ref=1)
    assert semantics.obligation_status("read:missing") is ObligationStatus.WAIVED


def test_local_read_failure_cannot_block_before_matching_fallback_is_satisfied() -> None:
    semantics = _local_failure_fallback_semantics()

    with pytest.raises(TaskSemanticsError):
        semantics.block("read:missing", evidence_ref=1)

    assert semantics.obligation_status("read:missing") is ObligationStatus.PENDING
    assert semantics.obligation_status("fallback:missing") is ObligationStatus.PENDING

    semantics.satisfy("fallback:missing", evidence_ref=1)
    semantics.waive("read:missing", evidence_ref=1)

    assert semantics.obligation_status("fallback:missing") is ObligationStatus.SATISFIED
    assert semantics.obligation_status("read:missing") is ObligationStatus.WAIVED


def test_unrecovered_local_read_failure_can_still_be_blocked() -> None:
    semantics = _local_failure_read_only_semantics()

    semantics.block("read:missing.txt", evidence_ref=1)

    assert semantics.obligation_status("read:missing.txt") is ObligationStatus.BLOCKED


def test_unrelated_fallback_does_not_suppress_legitimate_read_block() -> None:
    semantics = TaskSemantics(
        TaskIntent("Leia a.txt; se necessario, relate a falha de b.txt."),
        [
            TaskObligation("read:a", "read", "Ler a.txt.", target="a.txt"),
            TaskObligation(
                "fallback:b",
                "fallback",
                "Relatar falha de b.txt.",
                fallback_target="b.txt",
            ),
        ],
        _strict_evidence=True,
    )
    semantics.register_observation(
        "file_reader",
        {
            "ok": False,
            "done": True,
            "executed": True,
            "status": "failed",
            "error": "arquivo nao encontrado",
        },
        evidence_ref=1,
        args={"file_path": "a.txt"},
    )

    semantics.block("read:a", evidence_ref=1)

    assert semantics.obligation_status("read:a") is ObligationStatus.BLOCKED
    assert semantics.obligation_status("fallback:b") is ObligationStatus.PENDING


def test_primary_failure_cannot_prove_fallback_blocked() -> None:
    semantics = _local_failure_fallback_semantics()

    with pytest.raises(TaskSemanticsError):
        semantics.block("fallback:missing", evidence_ref=1)

    assert semantics.obligation_status("fallback:missing") is ObligationStatus.PENDING


def _forged_local_failure_fallback_checkpoint(
    *,
    read_status: str = "waived",
    fallback_status: str = "satisfied",
) -> dict[str, object]:
    objective = "Leia missing.txt; se nao puder, relate a falha."
    state = _state()
    state.objective = objective
    state.set_task_semantics(_local_failure_fallback_semantics())
    state.record_tool_result(
        "file_reader",
        {"file_path": "missing.txt"},
        {
            "ok": False,
            "done": True,
            "executed": True,
            "status": "failed",
            "error": "arquivo nao encontrado",
        },
    )
    checkpoint = state.to_checkpoint_dict()
    semantics = checkpoint["task_semantics"]
    assert isinstance(semantics, dict)
    statuses = semantics["statuses"]
    assert isinstance(statuses, dict)
    statuses["read:missing"] = read_status
    statuses["fallback:missing"] = fallback_status
    return checkpoint


@pytest.mark.parametrize("forged_status", ("satisfied", "blocked"))
def test_checkpoint_rejects_forged_recovered_read_status(forged_status: str) -> None:
    checkpoint = _forged_local_failure_fallback_checkpoint(read_status=forged_status)

    with pytest.raises(ValueError, match="task semantics evidence"):
        _state().from_checkpoint_dict(checkpoint)


def test_checkpoint_rejects_forged_fallback_blocked_status() -> None:
    checkpoint = _forged_local_failure_fallback_checkpoint(fallback_status="blocked")

    with pytest.raises(ValueError, match="task semantics evidence"):
        _state().from_checkpoint_dict(checkpoint)


def _forged_compare_checkpoint(
    history: list[tuple[str, str]],
    evidence_refs: list[int],
) -> dict[str, object]:
    state = _state()
    objective = "Compare a.txt e b.txt."
    state.objective = objective
    state.set_task_semantics(TaskSemantics.from_objective(objective))
    for path, value in history:
        state.record_tool_result(
            "file_reader",
            {"file_path": path},
            _complete_source(value, path),
        )
    checkpoint = state.to_checkpoint_dict()
    semantics = checkpoint["task_semantics"]
    assert isinstance(semantics, dict)
    statuses = semantics["statuses"]
    evidence = semantics["evidence"]
    assert isinstance(statuses, dict) and isinstance(evidence, dict)
    statuses["requirement:compare"] = "satisfied"
    evidence["requirement:compare"] = evidence_refs
    return checkpoint


@pytest.mark.parametrize("evidence_refs", ([1], [2]))
def test_compare_restore_rejects_one_operand_only(evidence_refs: list[int]) -> None:
    history = [("a.txt", "A")] if evidence_refs == [1] else [("b.txt", "B")]
    restored = _state()

    with pytest.raises(ValueError, match="task semantics evidence"):
        restored.from_checkpoint_dict(_forged_compare_checkpoint(history, evidence_refs))


def test_compare_restore_accepts_exact_two_operand_evidence() -> None:
    checkpoint = _forged_compare_checkpoint(
        [("a.txt", "A"), ("b.txt", "B")],
        [1, 2],
    )
    restored = _state()

    restored.from_checkpoint_dict(checkpoint)

    assert restored.obligation_status("requirement:compare") is ObligationStatus.SATISFIED
    assert restored.task_semantics.obligation_evidence("requirement:compare") == (1, 2)


@pytest.mark.parametrize("evidence_refs", ([1, 2], [1, 1]))
def test_compare_restore_rejects_refs_that_do_not_cover_both_operands(
    evidence_refs: list[int],
) -> None:
    history = [("a.txt", "A"), ("a.txt", "A again")]
    restored = _state()

    with pytest.raises(ValueError, match="task semantics evidence"):
        restored.from_checkpoint_dict(_forged_compare_checkpoint(history, evidence_refs))


def test_effect_restore_rejects_file_reader_provenance() -> None:
    state, authority = _effect_state("file_reader", _complete("A"), {"read"})
    checkpoint = state.to_checkpoint_dict()
    semantics = checkpoint["task_semantics"]
    assert isinstance(semantics, dict)
    statuses = semantics["statuses"]
    evidence = semantics["evidence"]
    assert isinstance(statuses, dict) and isinstance(evidence, dict)
    statuses["effect:write"] = "satisfied"
    evidence["effect:write"] = [1]

    with pytest.raises(ValueError, match="task semantics evidence"):
        _state().from_checkpoint_dict(checkpoint, effect_authority=authority)


@pytest.mark.parametrize(
    "authority",
    (
        SimpleNamespace(),
        SimpleNamespace(tool_registry=_Registry({})),
        SimpleNamespace(tool_registry=_MalformedRegistry()),
        SimpleNamespace(tool_registry=_BrokenRegistry()),
    ),
)
def test_effect_waiver_rejects_unknown_capability(authority: SimpleNamespace) -> None:
    state, _ = _effect_state("file_reader", _complete("A"), {"read"})

    with pytest.raises(TaskSemanticsError):
        state.waive_obligation(
            "effect:write",
            evidence_ref=1,
            effect_authority=authority,
        )


def test_effect_waiver_rejects_known_write_tool() -> None:
    state, authority = _effect_state("code_task", _complete("A"), {"write"})

    with pytest.raises(TaskSemanticsError):
        state.waive_obligation(
            "effect:write",
            evidence_ref=1,
            effect_authority=authority,
        )


@pytest.mark.parametrize("method", ("record_executed_effect", "waive_effect"))
def test_strict_effect_progression_rejects_synthetic_terminal_claims(method: str) -> None:
    for kwargs in ({}, {"allow_legacy": True}):
        state = _state()
        state.reset_task_progression(["write"])

        with pytest.raises(TaskSemanticsError):
            getattr(state, method)("write", **kwargs)

        assert state.obligation_status("effect:write") is ObligationStatus.PENDING
        assert state.executed_effects == []
        assert state.waived_effects == []
        assert state.pending_effects() == ("write",)


@pytest.mark.parametrize("attribute", ("executed_effects", "waived_effects"))
def test_strict_compatibility_setters_do_not_promote_effect_truth(attribute: str) -> None:
    state = _state()
    state.reset_task_progression(["write"])

    with pytest.raises(TaskSemanticsError):
        setattr(state, attribute, ["write"])

    assert state.obligation_status("effect:write") is ObligationStatus.PENDING
    assert state.executed_effects == []
    assert state.waived_effects == []
    assert state.pending_effects() == ("write",)


@pytest.mark.parametrize("method", ("record_effect", "waive_effect"))
def test_direct_strict_legacy_api_does_not_promote_effect_truth(method: str) -> None:
    semantics = TaskSemantics.from_legacy(
        "",
        ["write"],
        executed_effects=["write"],
        waived_effects=["write"],
    )

    with pytest.raises(TaskSemanticsError):
        getattr(semantics, method)("write", allow_legacy=True)

    assert semantics.obligation_status("effect:write") is ObligationStatus.PENDING
    assert semantics.executed_effects() == ()
    assert semantics.waived_effects() == ()
    assert semantics.pending_effects() == ("write",)


@pytest.mark.parametrize("method", ("record_executed_effect", "waive_effect"))
def test_strict_effect_rejects_legacy_ref_even_with_matching_observation(method: str) -> None:
    state, authority = _effect_state("code_task", _write_result(), {"write"})
    synthetic_ref = "legacy:effect:write"
    state.task_semantics.register_observation(
        "code_task",
        _write_result(),
        evidence_ref=synthetic_ref,
        args={},
    )

    with pytest.raises(TaskSemanticsError, match="sintetica"):
        getattr(state, method)(
            "write",
            evidence_ref=synthetic_ref,
            effect_authority=authority,
        )

    assert state.obligation_status("effect:write") is ObligationStatus.PENDING
    assert state.executed_effects == []
    assert state.waived_effects == []


def test_strict_constructor_does_not_promote_compatibility_effect_lists() -> None:
    semantics = TaskSemantics(
        TaskIntent("", ("write",)),
        [TaskObligation("effect:write", "effect", "write", effect="write")],
        executed_effects=["write"],
        waived_effects=["write"],
        _strict_evidence=True,
    )

    assert semantics.obligation_status("effect:write") is ObligationStatus.PENDING
    assert semantics.executed_effects() == ()
    assert semantics.waived_effects() == ()
    assert semantics.pending_effects() == ("write",)


def test_strict_constructor_rejects_synthetic_effect_status() -> None:
    with pytest.raises(TaskSemanticsError, match="sintetica"):
        TaskSemantics(
            TaskIntent("", ("write",)),
            [TaskObligation("effect:write", "effect", "write", effect="write")],
            statuses={"effect:write": "satisfied"},
            evidence={"effect:write": ["legacy:effect:write"]},
            _strict_evidence=True,
        )


@pytest.mark.parametrize(
    ("obligation", "intent"),
    (
        (
            TaskObligation("read:b", "read", "Ler b.txt.", target="b.txt"),
            TaskIntent("Leia b.txt."),
        ),
        (
            TaskObligation("search:x", "search", "Buscar X.", query="X"),
            TaskIntent("Busque X."),
        ),
        (
            TaskObligation(
                "compare:ab",
                "compare",
                "Comparar a.txt e b.txt.",
                operands=("a.txt", "b.txt"),
            ),
            TaskIntent("Compare a.txt e b.txt."),
        ),
        (
            TaskObligation("analyze:b", "analyze", "Analisar b.txt.", target="b.txt"),
            TaskIntent("Analise b.txt."),
        ),
        (
            TaskObligation(
                "fallback:b",
                "fallback",
                "Relatar a falha de b.txt.",
                fallback_target="b.txt",
            ),
            TaskIntent("Leia b.txt e relate a falha."),
        ),
        (
            TaskObligation("effect:write", "effect", "Escrever.", effect="write"),
            TaskIntent("write", ("write",)),
        ),
    ),
)
def test_strict_constructor_stages_numeric_terminal_claim_without_authority(
    obligation: TaskObligation,
    intent: TaskIntent,
) -> None:
    semantics = TaskSemantics(
        intent,
        [obligation],
        statuses={obligation.id: "satisfied"},
        evidence={obligation.id: [1]},
        _strict_evidence=True,
    )

    assert semantics.obligation_status(obligation.id) is ObligationStatus.PENDING
    assert semantics.obligation_evidence(obligation.id) == ()
    assert semantics.pending_obligations() == (obligation,)
    assert semantics.terminal_evidence_complete() is False
    assert semantics._evidence_catalog == {}

    restored = TaskSemantics.from_checkpoint_dict(semantics.to_checkpoint_dict())
    assert restored.obligation_status(obligation.id) is ObligationStatus.PENDING
    assert restored.obligation_evidence(obligation.id) == ()
    assert restored.terminal_evidence_complete() is False


@pytest.mark.parametrize("status", ("waived", "blocked"))
def test_isolated_checkpoint_stages_non_satisfied_terminal_claims(status: str) -> None:
    obligation = TaskObligation("read:b", "read", "Ler b.txt.", target="b.txt")
    checkpoint = TaskSemantics(
        TaskIntent("Leia b.txt."),
        [obligation],
        statuses={obligation.id: status},
        evidence={obligation.id: [1]},
        _strict_evidence=True,
    ).to_checkpoint_dict()

    restored = TaskSemantics.from_checkpoint_dict(checkpoint)

    assert restored.obligation_status(obligation.id) is ObligationStatus.PENDING
    assert restored.pending_obligations() == (obligation,)
    assert restored.terminal_evidence_complete() is False


def test_agent_state_rejects_non_strict_semantics_owner() -> None:
    state = _state()
    non_strict = TaskSemantics(
        TaskIntent("Leia b.txt."),
        [TaskObligation("read:b", "read", "Ler b.txt.", target="b.txt")],
    )

    with pytest.raises(TypeError):
        state.set_task_semantics(non_strict)

    assert state.task_semantics._strict_evidence is True


def test_replacing_effect_contract_cannot_reuse_old_terminal_projection() -> None:
    state, authority = _effect_state("code_task", _write_result(), {"write"})
    refresh_executed_effects(authority)
    assert state.pending_effects() == ()

    state.requested_effects = []
    state.requested_effects = ["write"]

    assert state.obligation_status("effect:write") is ObligationStatus.PENDING
    assert state.pending_effects() == ("write",)


def test_effect_refresh_requires_write_capability_and_execution() -> None:
    not_write, not_write_authority = _effect_state("file_reader", _write_result(), {"read"})
    refresh_executed_effects(not_write_authority)
    assert not_write.task_semantics.obligation_status("effect:write") is ObligationStatus.PENDING

    not_executed, not_executed_authority = _effect_state(
        "code_task",
        _write_result(executed=False),
        {"write"},
    )
    refresh_executed_effects(not_executed_authority)
    assert not_executed.task_semantics.obligation_status("effect:write") is ObligationStatus.PENDING


def test_effect_write_authority_survives_checkpoint_round_trip() -> None:
    state, authority = _effect_state("code_task", _write_result(), {"write"})
    refresh_executed_effects(authority)
    assert state.task_semantics.obligation_status("effect:write") is ObligationStatus.SATISFIED

    restored = _state()
    restored.from_checkpoint_dict(state.to_checkpoint_dict(), effect_authority=authority)

    assert restored.task_semantics.obligation_status("effect:write") is ObligationStatus.SATISFIED
    assert restored.executed_effects == ["write"]


def test_modern_restore_reconstructs_unbound_canonical_write_projection() -> None:
    state = _unbound_write_state()
    restored = _state()

    restored.from_checkpoint_dict(
        state.to_checkpoint_dict(),
        effect_authority=_write_authority(),
    )

    assert restored.task_semantics.obligations == ()
    assert restored.executed_effects == ["write"]
    assert restored.prohibited_effects_occurred() == ("write",)


def test_modern_complete_restore_rejects_prohibited_unbound_write_transactionally() -> None:
    state = _unbound_write_state()
    checkpoint = state.to_checkpoint_dict()
    checkpoint["terminal_disposition"] = "complete"
    target = _state()
    target.objective = "before"
    target.terminal_disposition = "block"
    before = copy.deepcopy(target.to_checkpoint_dict())

    with pytest.raises(ValueError, match="conflicts with pending semantics"):
        target.from_checkpoint_dict(
            checkpoint,
            effect_authority=_write_authority(),
        )

    assert target.to_checkpoint_dict() == before


@pytest.mark.parametrize(
    ("authority", "result"),
    (
        (SimpleNamespace(), _write_result()),
        (SimpleNamespace(tool_registry=_MalformedRegistry()), _write_result()),
        (SimpleNamespace(tool_registry=_Registry({"code_task": {"read"}})), _write_result()),
        (
            SimpleNamespace(tool_registry=_Registry({"code_task": {"write"}})),
            _write_result(executed=False),
        ),
        (
            SimpleNamespace(tool_registry=_Registry({"code_task": {"write"}})),
            _write_result(status="permission_denied"),
        ),
        (
            SimpleNamespace(tool_registry=_Registry({"code_task": {"write"}})),
            {"ok": True, "done": True, "executed": True, "status": "succeeded"},
        ),
    ),
)
def test_modern_restore_does_not_reconstruct_unproven_unbound_write(
    authority: SimpleNamespace,
    result: dict[str, object],
) -> None:
    state = _unbound_write_state(result)
    restored = _state()

    restored.from_checkpoint_dict(
        state.to_checkpoint_dict(),
        effect_authority=authority,
    )

    assert restored.executed_effects == []
    assert restored.prohibited_effects_occurred() == ()


def test_effect_waived_and_blocked_use_distinct_canonical_evidence() -> None:
    waived, waiver_authority = _effect_state("file_reader", _complete("A"), {"read"})
    assert [index for index, _ in eligible_waiver_observations(waiver_authority)] == [1]
    waived.waive_obligation(
        "effect:write",
        evidence_ref=1,
        effect_authority=waiver_authority,
    )
    restored_waiver = _state()
    restored_waiver.from_checkpoint_dict(
        waived.to_checkpoint_dict(),
        effect_authority=waiver_authority,
    )
    assert restored_waiver.obligation_status("effect:write") is ObligationStatus.WAIVED

    blocked, block_authority = _effect_state(
        "code_task",
        _write_result(executed=False, status="permission_denied"),
        {"write"},
    )
    blocked.block_obligation(
        "effect:write",
        evidence_ref=1,
        effect_authority=block_authority,
    )
    restored_block = _state()
    restored_block.from_checkpoint_dict(
        blocked.to_checkpoint_dict(),
        effect_authority=block_authority,
    )
    assert restored_block.obligation_status("effect:write") is ObligationStatus.BLOCKED


def test_legacy_executed_effect_without_history_stays_pending() -> None:
    restored = _state()

    restored.from_checkpoint_dict(_legacy_checkpoint(executed=["write"]))

    assert restored.executed_effects == []
    assert restored.pending_effects() == ("write",)
    assert restored.obligation_status("effect:write") is ObligationStatus.PENDING


def test_legacy_null_semantics_cannot_hide_pending_complete_disposition() -> None:
    checkpoint = _legacy_checkpoint(executed=["write"])
    checkpoint["terminal_disposition"] = "complete"

    with pytest.raises(ValueError, match="conflicts with pending semantics"):
        _state().from_checkpoint_dict(checkpoint)


def test_legacy_waived_effect_without_provenance_stays_pending() -> None:
    restored = _state()

    restored.from_checkpoint_dict(_legacy_checkpoint(waived=["write"]))

    assert restored.waived_effects == []
    assert restored.pending_effects() == ("write",)


def test_legacy_history_reconstructs_non_effect_terminal_state() -> None:
    restored = _state()
    checkpoint = _legacy_checkpoint(
        objective="Leia a.txt.",
        history=[
            {
                "tool": "file_reader",
                "args": {"file_path": "a.txt"},
                "result": _complete_source("A", "a.txt"),
            }
        ],
    )
    checkpoint["requested_effects"] = []

    restored.from_checkpoint_dict(checkpoint)

    assert restored.obligation_status("read:1") is ObligationStatus.SATISFIED


def test_legacy_history_reconstructs_effect_only_through_live_authority() -> None:
    restored = _state()
    checkpoint = _legacy_checkpoint(
        history=[
            {
                "tool": "code_task",
                "args": {},
                "result": _write_result(),
            }
        ],
    )
    authority = SimpleNamespace(
        tool_registry=_Registry({"code_task": {"write"}}),
    )

    restored.from_checkpoint_dict(checkpoint, effect_authority=authority)

    assert restored.obligation_status("effect:write") is ObligationStatus.SATISFIED
    assert restored.executed_effects == ["write"]


def test_previous_read_runtime_and_checkpoint_require_prior_matching_read() -> None:
    state = _previous_read_state()
    state.record_tool_result(
        "file_reader",
        {"file_path": "fonte.txt"},
        _complete_source("orion", "fonte.txt"),
    )
    state.record_tool_result(
        "grep",
        {"path": ".", "pattern": "orion"},
        _complete_source([], "."),
    )

    assert state.obligation_status("search:previous") is ObligationStatus.SATISFIED
    restored = _state()
    restored.from_checkpoint_dict(state.to_checkpoint_dict())
    assert restored.obligation_status("search:previous") is ObligationStatus.SATISFIED


def test_previous_read_does_not_use_a_future_read_on_restore() -> None:
    state = _previous_read_state()
    state.record_tool_result(
        "grep",
        {"path": ".", "pattern": "orion"},
        _complete([]),
    )
    state.record_tool_result(
        "file_reader",
        {"file_path": "fonte.txt"},
        _complete_source("orion", "fonte.txt"),
    )
    checkpoint = state.to_checkpoint_dict()
    semantics = checkpoint["task_semantics"]
    assert isinstance(semantics, dict)
    statuses = semantics["statuses"]
    evidence = semantics["evidence"]
    assert isinstance(statuses, dict) and isinstance(evidence, dict)
    statuses["search:previous"] = "satisfied"
    evidence["search:previous"] = [1]

    with pytest.raises(ValueError, match="task semantics evidence"):
        _state().from_checkpoint_dict(checkpoint)


def test_previous_read_rejects_different_value_and_accepts_only_matching_prior_read() -> None:
    wrong = _previous_read_state()
    wrong.record_tool_result(
        "file_reader",
        {"file_path": "fonte.txt"},
        _complete("andromeda"),
    )
    wrong.record_tool_result(
        "grep",
        {"path": ".", "pattern": "orion"},
        _complete_source([], "."),
    )
    assert wrong.obligation_status("search:previous") is ObligationStatus.PENDING

    multiple = _previous_read_state()
    multiple.record_tool_result(
        "file_reader",
        {"file_path": "antiga.txt"},
        _complete_source("andromeda", "antiga.txt"),
    )
    multiple.record_tool_result(
        "file_reader",
        {"file_path": "fonte.txt"},
        _complete_source("orion", "fonte.txt"),
    )
    multiple.record_tool_result(
        "grep",
        {"path": ".", "pattern": "orion"},
        _complete_source([], "."),
    )
    assert multiple.obligation_status("search:previous") is ObligationStatus.SATISFIED


def test_legacy_previous_read_replay_does_not_use_future_history() -> None:
    objective = "Leia fonte_h2.txt e depois procure nos outros arquivos pela palavra que ele contem."
    common = {
        "objective": objective,
        "requested_effects": [],
        "executed_effects": [],
        "waived_effects": [],
        "prohibited_effects": [],
        "plan": [],
        "step_records": [],
        "task_semantics": None,
        "events": [],
        "conversation_history": [],
    }
    valid = dict(common)
    valid["tool_history"] = [
            {"tool": "file_reader", "args": {"file_path": "fonte_h2.txt"}, "result": _complete_source("orion", "fonte_h2.txt")},
            {"tool": "grep", "args": {"path": ".", "pattern": "orion"}, "result": _complete_source([], ".")},
    ]
    restored_valid = _state()
    restored_valid.from_checkpoint_dict(valid)
    valid_search = next(item for item in restored_valid.task_obligations if item.kind == "search")
    assert restored_valid.obligation_status(valid_search.id) is ObligationStatus.SATISFIED

    future = dict(common)
    future["tool_history"] = [
        {"tool": "grep", "args": {"path": ".", "pattern": "orion"}, "result": _complete_source([], ".")},
        {"tool": "file_reader", "args": {"file_path": "fonte_h2.txt"}, "result": _complete_source("orion", "fonte_h2.txt")},
    ]
    restored_future = _state()
    restored_future.from_checkpoint_dict(future)
    future_search = next(item for item in restored_future.task_obligations if item.kind == "search")
    assert restored_future.obligation_status(future_search.id) is ObligationStatus.PENDING


def test_synthetic_effect_checkpoint_is_rejected_before_reentry() -> None:
    state = _state()
    state.reset_task_progression(["write"])
    checkpoint = state.task_semantics.to_checkpoint_dict()
    statuses = checkpoint["statuses"]
    evidence = checkpoint["evidence"]
    assert isinstance(statuses, dict) and isinstance(evidence, dict)
    statuses["effect:write"] = "satisfied"
    evidence["effect:write"] = ["legacy:effect:write"]

    with pytest.raises(TaskSemanticsError, match="sintetica"):
        TaskSemantics.from_checkpoint_dict(checkpoint)


def test_rejected_checkpoint_does_not_publish_partial_authoritative_state() -> None:
    original = _state()
    original.objective = "before"
    original.terminal_disposition = "block"
    before = copy.deepcopy(original.to_checkpoint_dict())
    checkpoint = copy.deepcopy(_canonical_read_checkpoint())
    checkpoint["terminal_disposition"] = "complete"
    checkpoint["step_records"] = [
        {"step_id": "forged", "status": "completed", "attempts": 0, "last_error": ""}
    ]

    with pytest.raises(ValueError):
        original.from_checkpoint_dict(checkpoint)

    assert original.to_checkpoint_dict() == before
    assert original.terminal_disposition == "block"
    assert original.task_semantics.terminal_evidence_complete() is True
