from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.execution_state import StepExecutionRecord, StepStatus
from agent.final_response import compose_operational_answer
from agent.planning.plan_builder import PlanBuilder, PlanBuildResult, PlanningDecisionKind
from agent.planning.plan_execution_loop import run_plan_loop
from agent.planning.reasoning_boundary import (
    continue_after_reasoning_boundary as run_reasoning_boundary,
)
from agent.planning.task_completion import (
    allow_linear_completion,
    continue_after_observation,
    continue_after_reasoning_boundary,
    initialize_task_progression,
    refresh_executed_effects,
    review_task_completion,
)
from agent.planning.task_semantics import TaskSemanticsError
from agent.reporting.operational_outcome import project_operational_outcome
from agent.reporting.run_receipt import build_run_receipt
from agent.state import AgentState


@pytest.mark.parametrize(
    "objective",
    [
        "Não altere nenhum arquivo.",
        "Leia a.txt. Não modifique nada.",
        "Do not modify any files.",
        "Se X for verdadeiro, escreva Y; caso contrário, não altere nada.",
        "Escreva exatamente o texto abaixo.",
        "Write exactly the text below.",
    ],
)
def test_initialize_task_progression_uses_canonical_effect_inference(objective):
    state = AgentState()
    orchestrator = SimpleNamespace(agent_state=state)

    initialize_task_progression(orchestrator, objective)

    expected = ["write"] if "escreva Y" in objective else []
    assert state.requested_effects == expected


class _Registry:
    @staticmethod
    def descriptor(_tool_name: str):
        return SimpleNamespace(capabilities=frozenset({"write"}))


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (
            {
                "applied": True,
                "mutation_occurred": True,
                "final_state": "applied",
            },
            ["write"],
        ),
        (
            {
                "applied": True,
                "mutation_occurred": False,
                "final_state": "applied",
            },
            [],
        ),
        (
            {
                "applied": True,
                "mutation_occurred": True,
                "rollback_occurred": True,
                "final_state": "restored",
            },
            [],
        ),
        ({}, []),
    ],
)
def test_write_completion_requires_canonical_applied_artifact(metadata, expected):
    state = AgentState()
    state.tool_history = [
        {
            "tool": "code_task",
            "result": {
                "executed": True,
                "data": {"artifacts": [{"metadata": metadata}]},
            },
        }
    ]
    orchestrator = SimpleNamespace(agent_state=state, tool_registry=_Registry())

    refresh_executed_effects(orchestrator)

    assert state.executed_effects == expected


def test_aggregate_failure_does_not_return_a_later_success_message() -> None:
    state = AgentState()
    state.last_result = {
        "ok": True,
        "status": "succeeded",
        "message": "Arquivo alterado com sucesso.",
    }
    orchestrator = SimpleNamespace(
        agent_state=state,
        tool_registry=None,
        _task_failed=True,
        _emit=lambda *_args, **_kwargs: None,
    )

    answer = allow_linear_completion(orchestrator, "objetivo")

    assert answer == "A tarefa não pôde ser concluída."
    assert state.terminal_disposition == "fail"


def test_failed_step_record_cannot_be_overwritten_by_later_success() -> None:
    state = AgentState()
    state.last_result = {"ok": True, "status": "succeeded", "message": "feito"}
    state.step_records = {
        "failed": StepExecutionRecord("failed", status=StepStatus.FAILED),
        "later": StepExecutionRecord("later", status=StepStatus.COMPLETED),
    }
    orchestrator = SimpleNamespace(
        agent_state=state,
        tool_registry=None,
        _task_failed=False,
        _emit=lambda *_args, **_kwargs: None,
    )

    answer = allow_linear_completion(orchestrator, "objetivo")

    assert answer == "A tarefa não pôde ser concluída."
    assert state.terminal_disposition == "fail"


def test_complete_disposition_cannot_bypass_pending_effect() -> None:
    state = AgentState()
    state.requested_effects = ["write"]
    state.terminal_disposition = "complete"
    orchestrator = SimpleNamespace(
        agent_state=state,
        tool_registry=None,
        _task_failed=False,
        _cancelled=False,
        _emit=lambda *_args, **_kwargs: None,
    )

    answer = allow_linear_completion(orchestrator, "altere o arquivo")

    assert "permanece pendente" in answer
    assert state.terminal_disposition == "block"
    assert state.last_result["status"] == "blocked"


def test_compatibility_effect_claim_cannot_pass_completion_review() -> None:
    state = AgentState()
    state.requested_effects = ["write"]
    with pytest.raises(TaskSemanticsError):
        state.executed_effects = ["write"]

    review = review_task_completion(
        SimpleNamespace(
            agent_state=state,
            tool_registry=None,
            _task_failed=False,
            _cancelled=False,
        )
    )

    assert review.accepted is False
    assert review.reason_code == "requested_effect_pending"
    assert state.pending_effects() == ("write",)


def test_continuation_owner_failure_is_bounded_and_blocks_pending_effect() -> None:
    state = AgentState()
    state.requested_effects = ["write"]
    calls = []

    def fail_owner(*_args, **_kwargs):
        calls.append(True)
        raise RuntimeError("planner indisponivel")

    orchestrator = SimpleNamespace(
        agent_state=state,
        plan_builder=SimpleNamespace(continue_after_observation=fail_owner),
        _emit=lambda *_args, **_kwargs: None,
        _task_failed=False,
        _cancelled=False,
    )

    answer = continue_after_observation(orchestrator, "altere o arquivo")

    assert answer == "A tarefa não foi concluída: o efeito solicitado permanece pendente."
    assert calls == [True]
    assert state.continuation_attempts == 1
    assert state.pending_effects() == ("write",)
    assert state.last_result["status"] == "blocked"


def test_reasoning_boundary_extends_once_without_marking_prefix_complete() -> None:
    state = AgentState()
    state.set_plan([{"tool": "file_reader", "args": {}}])
    state.mark_step_completed(0)
    calls = []

    def boundary_owner(objective):
        calls.append(objective)
        return PlanBuildResult(plan=[{"tool": "grep", "args": {}}])

    class _Gateway:
        def extend_validated_plan(self, plan, objective):
            calls.append((plan, objective))
            return plan

    orchestrator = SimpleNamespace(
        agent_state=state,
        plan_builder=SimpleNamespace(continue_after_reasoning_boundary=boundary_owner),
        execution_gateway=_Gateway(),
        _emit=lambda *_args, **_kwargs: None,
        _task_failed=False,
        _cancelled=False,
        tool_registry=None,
    )

    outcome = continue_after_reasoning_boundary(orchestrator, "interprete a observação")

    assert outcome.extended is True
    assert outcome.answer is None
    assert outcome.completed is False
    assert state.reasoning_turns_used == 1
    assert calls[0] == "interprete a observação"


def test_reasoning_boundary_budget_blocks_second_transition() -> None:
    state = AgentState()
    state.reasoning_turns_used = 3
    calls = []
    orchestrator = SimpleNamespace(
        agent_state=state,
        plan_builder=SimpleNamespace(
            continue_after_reasoning_boundary=lambda *_args: calls.append(True)
        ),
        _emit=lambda *_args, **_kwargs: None,
        tool_registry=None,
    )

    outcome = continue_after_reasoning_boundary(orchestrator, "objetivo")

    assert outcome.answer == "A tarefa não pôde prosseguir após a fronteira de raciocínio."
    assert calls == []
    assert state.last_result["status"] == "blocked"


def test_reasoning_boundary_uses_canonical_configured_budget() -> None:
    state = AgentState()
    state.reasoning_turns_used = 2
    calls = []
    orchestrator = SimpleNamespace(
        agent_state=state,
        session=SimpleNamespace(config={"max_reasoning_turns": 2}),
        plan_builder=SimpleNamespace(
            continue_after_reasoning_boundary=lambda *_args: calls.append(True)
        ),
        _emit=lambda *_args, **_kwargs: None,
        tool_registry=None,
    )

    outcome = continue_after_reasoning_boundary(orchestrator, "objetivo")

    assert outcome.blocked is True
    assert calls == []
    assert state.reasoning_turns_used == 2


def test_reasoning_and_pending_effect_counters_remain_independent() -> None:
    state = AgentState()
    state.requested_effects = ["write"]
    state.tool_history = [{
        "tool": "file_reader",
        "invocation_id": "read-1",
        "result": {
            "ok": True,
            "done": True,
            "status": "succeeded",
            "executed": True,
            "data": "modificado",
        },
    }]

    class _Registry:
        @staticmethod
        def descriptor(_tool_name):
            return SimpleNamespace(capabilities=frozenset({"read"}))

    orchestrator = SimpleNamespace(
        agent_state=state,
        session=SimpleNamespace(config={"max_reasoning_turns": 2}),
        plan_builder=SimpleNamespace(
            continue_after_reasoning_boundary=lambda _objective: PlanBuildResult(
                plan=[{"tool": "directory_lister", "args": {}}]
            ),
            continue_after_observation=lambda *_args: PlanBuildResult(
                waiver_observation_index=1,
                kind=PlanningDecisionKind.COMPLETE,
            ),
        ),
        execution_gateway=SimpleNamespace(
            extend_validated_plan=lambda plan, _objective: plan,
        ),
        tool_registry=_Registry(),
        _emit=lambda *_args, **_kwargs: None,
        _task_failed=False,
        _cancelled=False,
    )

    assert continue_after_reasoning_boundary(orchestrator, "objetivo").extended is True
    assert state.reasoning_turns_used == 1
    assert state.continuation_attempts == 0

    assert continue_after_observation(orchestrator, "objetivo") is None
    assert state.reasoning_turns_used == 1
    assert state.continuation_attempts == 1
    assert state.pending_effects() == ()


def test_reasoning_boundary_blocks_without_new_history_progress() -> None:
    state = AgentState()
    state.set_plan([{"tool": "file_reader", "args": {}}])
    state.mark_step_completed(0)
    calls = []

    def boundary_owner(_objective):
        calls.append(True)
        return PlanBuildResult(plan=[{"tool": "grep", "args": {}}])

    class _Gateway:
        def extend_validated_plan(self, plan, objective):
            del objective
            return plan

    orchestrator = SimpleNamespace(
        agent_state=state,
        plan_builder=SimpleNamespace(continue_after_reasoning_boundary=boundary_owner),
        execution_gateway=_Gateway(),
        _emit=lambda *_args, **_kwargs: None,
        _task_failed=False,
        _cancelled=False,
        tool_registry=None,
    )

    assert continue_after_reasoning_boundary(orchestrator, "objetivo").extended is True
    blocked = continue_after_reasoning_boundary(orchestrator, "objetivo")

    assert blocked.answer is not None
    assert calls == [True]
    assert state.reasoning_turns_used == 1


def test_reasoning_progress_ignores_fresh_step_and_invocation_ids() -> None:
    state = AgentState()
    state.tool_history = [
        {
            "step_id": "first-step",
            "invocation_id": "first-invocation",
            "tool": "grep",
            "args": {"pattern": "foo", "path": "."},
            "result": {"status": "succeeded", "ok": True, "executed": True, "data": []},
        }
    ]
    calls = []

    def boundary_owner(_objective):
        calls.append(True)
        return PlanBuildResult(plan=[{"tool": "grep", "args": {}}])

    class _Gateway:
        def extend_validated_plan(self, plan, objective):
            del objective
            return plan

    orchestrator = SimpleNamespace(
        agent_state=state,
        plan_builder=SimpleNamespace(continue_after_reasoning_boundary=boundary_owner),
        execution_gateway=_Gateway(),
        _emit=lambda *_args, **_kwargs: None,
        _task_failed=False,
        _cancelled=False,
        tool_registry=None,
    )

    assert continue_after_reasoning_boundary(orchestrator, "objetivo").extended is True
    state.tool_history.append(
        {
            "step_id": "second-step",
            "invocation_id": "second-invocation",
            "tool": "grep",
            "args": {"pattern": "foo", "path": "."},
            "result": {"status": "succeeded", "ok": True, "executed": True, "data": []},
        }
    )

    blocked = continue_after_reasoning_boundary(orchestrator, "objetivo")

    assert blocked.answer is not None
    assert calls == [True]
    assert state.reasoning_turns_used == 1


def test_reasoning_progress_changes_when_canonical_result_changes() -> None:
    state = AgentState()
    state.tool_history = [
        {
            "step_id": "first-step",
            "invocation_id": "first-invocation",
            "tool": "grep",
            "args": {"pattern": "foo", "path": "."},
            "result": {"status": "succeeded", "ok": True, "executed": True, "data": []},
        }
    ]
    calls = []

    def boundary_owner(_objective):
        calls.append(True)
        return PlanBuildResult(plan=[{"tool": "grep", "args": {}}])

    class _Gateway:
        def extend_validated_plan(self, plan, objective):
            del objective
            return plan

    orchestrator = SimpleNamespace(
        agent_state=state,
        plan_builder=SimpleNamespace(continue_after_reasoning_boundary=boundary_owner),
        execution_gateway=_Gateway(),
        _emit=lambda *_args, **_kwargs: None,
        _task_failed=False,
        _cancelled=False,
        tool_registry=None,
    )

    assert continue_after_reasoning_boundary(orchestrator, "objetivo").extended is True
    state.tool_history.append(
        {
            "step_id": "second-step",
            "invocation_id": "second-invocation",
            "tool": "grep",
            "args": {"pattern": "foo", "path": "."},
            "result": {"status": "succeeded", "ok": True, "executed": True, "data": ["match"]},
        }
    )

    assert continue_after_reasoning_boundary(orchestrator, "objetivo").extended is True
    assert calls == [True, True]
    assert state.reasoning_turns_used == 2


def _reasoning_boundary_orchestrator(state: AgentState, calls: list[str]) -> SimpleNamespace:
    def boundary_owner(objective: str) -> PlanBuildResult:
        calls.append(objective)
        return PlanBuildResult(plan=[{"tool": "grep", "args": {}}])

    class _Gateway:
        def extend_validated_plan(self, plan, objective):
            del objective
            return plan

    return SimpleNamespace(
        agent_state=state,
        plan_builder=SimpleNamespace(continue_after_reasoning_boundary=boundary_owner),
        execution_gateway=_Gateway(),
        _emit=lambda *_args, **_kwargs: None,
        session=SimpleNamespace(config={}),
    )


def _semantic_history_entry(result: object, *, invocation_id: str = "invocation") -> dict:
    return {
        "tool": "grep",
        "args": {"pattern": "foo", "path": "."},
        "step_id": f"step-{invocation_id}",
        "invocation_id": invocation_id,
        "result": {
            "status": "succeeded",
            "ok": True,
            "executed": True,
            "data": result,
        },
    }


def test_reasoning_window_excludes_pre_boundary_history_noise() -> None:
    state = AgentState()
    state.tool_history = [_semantic_history_entry(["old"], invocation_id="old")]
    calls: list[str] = []
    orchestrator = _reasoning_boundary_orchestrator(state, calls)

    initialize_task_progression(orchestrator, "interprete a observação")

    outcome = run_reasoning_boundary(orchestrator, "interprete a observação")

    assert outcome.blocked is True
    assert calls == []
    assert state.reasoning_turns_used == 0
    assert state.reasoning_last_history_count == 1


def test_reasoning_window_accepts_post_boundary_activity() -> None:
    state = AgentState()
    state.tool_history = [_semantic_history_entry(["old"], invocation_id="old")]
    calls: list[str] = []
    orchestrator = _reasoning_boundary_orchestrator(state, calls)

    initialize_task_progression(orchestrator, "interprete a observação")
    state.tool_history.append(_semantic_history_entry(["new"], invocation_id="new"))

    outcome = run_reasoning_boundary(orchestrator, "interprete a observação")

    assert outcome.extended is True
    assert calls == ["interprete a observação"]
    assert state.reasoning_turns_used == 1
    assert state.reasoning_last_history_count == 2


def test_reasoning_window_checkpoint_resume_and_new_task_reset() -> None:
    state = AgentState()
    state.tool_history = [_semantic_history_entry(["old"], invocation_id="old")]
    calls: list[str] = []
    orchestrator = _reasoning_boundary_orchestrator(state, calls)
    initialize_task_progression(orchestrator, "interprete a observação")
    state.tool_history.append(_semantic_history_entry(["new"], invocation_id="new"))

    assert run_reasoning_boundary(orchestrator, "interprete a observação").extended is True
    checkpoint = state.to_checkpoint_dict()

    restored = AgentState()
    restored.from_checkpoint_dict(checkpoint)
    resumed_calls: list[str] = []
    resumed = _reasoning_boundary_orchestrator(restored, resumed_calls)
    restored.tool_history.append(_semantic_history_entry(["after-resume"], invocation_id="resume"))

    assert run_reasoning_boundary(resumed, "interprete a observação").extended is True
    assert resumed_calls == ["interprete a observação"]
    assert restored.reasoning_turns_used == 2

    legacy_checkpoint = dict(checkpoint)
    legacy_checkpoint.pop("reasoning_last_history_count")
    legacy_restored = AgentState()
    legacy_restored.from_checkpoint_dict(legacy_checkpoint)
    assert legacy_restored.reasoning_last_history_count == len(legacy_restored.tool_history)

    restored.reset_task_progression()
    restored.tool_history = []
    assert restored.reasoning_turns_used == 0
    assert restored.reasoning_last_history_count == 0
    assert restored.reasoning_last_progress_token is None


def test_completion_policy_is_idempotent_after_terminal_projection() -> None:
    state = AgentState()
    events = []
    orchestrator = SimpleNamespace(
        agent_state=state,
        tool_registry=None,
        _task_failed=False,
        _cancelled=False,
        _emit=lambda kind, data=None: events.append((kind, data)),
    )

    assert allow_linear_completion(orchestrator, "objetivo") is None
    assert allow_linear_completion(orchestrator, "objetivo") is None
    assert state.terminal_disposition == "complete"
    assert [kind for kind, _ in events].count("task_outcome") == 1


def test_unverified_step_cannot_be_marked_complete() -> None:
    state = AgentState()
    state.set_plan(
        [{"tool": "file_reader", "args": {"file_path": "x.txt"}}]
    )
    state.mark_step_unverified(0, "evidence incomplete")
    state.last_result = {
        "ok": False,
        "done": True,
        "status": "unverified",
        "executed": False,
        "error": "evidence incomplete",
        "message": "evidence incomplete",
    }
    orchestrator = SimpleNamespace(
        agent_state=state,
        tool_registry=None,
        _task_failed=False,
        _cancelled=False,
        _emit=lambda *_args, **_kwargs: None,
    )

    answer = allow_linear_completion(orchestrator, "objective")

    assert answer is not None
    assert state.terminal_disposition != "complete"
    assert state.terminal_disposition == "unverified"
    assert state.last_result["status"] == "unverified"


def test_prohibited_observed_write_blocks_canonical_completion() -> None:
    state = AgentState()
    orchestrator = SimpleNamespace(
        agent_state=state,
        tool_registry=_Registry(),
        _task_failed=False,
        _cancelled=False,
        _emit=lambda *_args, **_kwargs: None,
    )
    initialize_task_progression(orchestrator, "Nao altere nenhum arquivo.")
    state.tool_history = [
        {
            "tool": "code_task",
            "result": {
                "ok": True,
                "status": "succeeded",
                "executed": True,
                "data": {
                    "artifacts": [
                        {
                            "metadata": {
                                "applied": True,
                                "mutation_occurred": True,
                                "final_state": "applied",
                            }
                        }
                    ]
                },
            },
        }
    ]

    answer = allow_linear_completion(orchestrator, "Nao altere nenhum arquivo.")

    assert answer == "A tarefa foi bloqueada: ocorreu um efeito proibido."
    assert state.terminal_disposition == "block"
    assert state.executed_effects == ["write"]


def test_reasoning_boundary_complete_rejects_pending_search_obligation() -> None:
    state = AgentState()
    initialize_task_progression(
        SimpleNamespace(agent_state=state),
        "Leia a fonte e procure o valor nos arquivos.",
    )
    state.record_tool_result(
        "file_reader",
        {"file_path": "fonte.txt"},
        {"ok": True, "done": True, "status": "succeeded", "data": "valor"},
    )
    orchestrator = SimpleNamespace(
        agent_state=state,
        session=SimpleNamespace(config={}),
        plan_builder=SimpleNamespace(
            continue_after_reasoning_boundary=lambda _objective: PlanBuildResult(
                kind=PlanningDecisionKind.COMPLETE,
            )
        ),
        _emit=lambda *_args, **_kwargs: None,
        _task_failed=False,
        _cancelled=False,
    )

    outcome = continue_after_reasoning_boundary(orchestrator, "Leia a fonte e procure o valor nos arquivos.")

    assert outcome.answer is not None
    assert outcome.completed is False
    assert state.terminal_disposition == "block"
    assert state.pending_obligations()


def test_reasoning_boundary_complete_accepts_satisfied_obligation() -> None:
    state = AgentState()
    initialize_task_progression(
        SimpleNamespace(agent_state=state),
        "Procure o valor nos arquivos.",
    )
    state.record_tool_result(
        "grep",
        {"path": ".", "pattern": "valor"},
        {"ok": True, "done": True, "status": "succeeded", "data": []},
    )
    orchestrator = SimpleNamespace(
        agent_state=state,
        session=SimpleNamespace(config={}),
        plan_builder=SimpleNamespace(
            continue_after_reasoning_boundary=lambda _objective: PlanBuildResult(
                kind=PlanningDecisionKind.COMPLETE,
            )
        ),
        _emit=lambda *_args, **_kwargs: None,
        _task_failed=False,
        _cancelled=False,
    )

    outcome = continue_after_reasoning_boundary(orchestrator, "Procure o valor nos arquivos.")

    assert outcome.completed is True
    assert outcome.answer is None
    assert state.terminal_disposition == "complete"


def test_same_operation_recovery_can_complete_after_failed_invocation() -> None:
    state = AgentState()
    state.step_records = {
        "step-a": StepExecutionRecord("step-a", status=StepStatus.FAILED),
    }
    args = {"file_path": "missing.txt"}
    state.tool_history = [
        {
            "step_id": "step-a",
            "tool": "file_reader",
            "args": args,
            "result": {"ok": False, "status": "failed", "error": "temporary"},
        },
        {
            "step_id": "step-a",
            "tool": "file_reader",
            "args": args,
            "result": {"ok": True, "done": True, "status": "succeeded", "data": "ok"},
        },
    ]
    state.last_result = state.tool_history[-1]["result"]
    orchestrator = SimpleNamespace(
        agent_state=state,
        tool_registry=None,
        _task_failed=True,
        _cancelled=False,
        _emit=lambda *_args, **_kwargs: None,
    )

    assert allow_linear_completion(orchestrator, "objetivo") is None
    assert state.terminal_disposition == "complete"


def test_unrelated_success_does_not_erase_failed_invocation() -> None:
    state = AgentState()
    state.step_records = {
        "step-a": StepExecutionRecord("step-a", status=StepStatus.FAILED),
    }
    state.tool_history = [
        {
            "step_id": "step-a",
            "tool": "file_reader",
            "args": {"file_path": "missing.txt"},
            "result": {"ok": False, "status": "failed", "error": "missing"},
        },
        {
            "step_id": "step-b",
            "tool": "echo",
            "args": {"text": "done"},
            "result": {"ok": True, "done": True, "status": "succeeded", "data": "done"},
        },
    ]
    state.last_result = state.tool_history[-1]["result"]
    orchestrator = SimpleNamespace(
        agent_state=state,
        tool_registry=None,
        _task_failed=False,
        _cancelled=False,
        _emit=lambda *_args, **_kwargs: None,
    )

    answer = allow_linear_completion(orchestrator, "objetivo")

    assert answer == "A tarefa não pôde ser concluída."
    assert state.terminal_disposition == "fail"


def test_empty_plan_still_crosses_boundary_before_success() -> None:
    state = AgentState()
    objective = "Procure o valor nos arquivos."
    initialize_task_progression(SimpleNamespace(agent_state=state), objective)
    state.record_tool_result(
        "grep",
        {"path": ".", "pattern": "valor"},
        {"ok": True, "done": True, "status": "succeeded", "data": []},
    )
    orchestrator = SimpleNamespace(
        agent_state=state,
        session=SimpleNamespace(config={}),
        plan_builder=SimpleNamespace(
            continue_after_reasoning_boundary=lambda _objective: PlanBuildResult(
                kind=PlanningDecisionKind.COMPLETE,
            )
        ),
        execution_gateway=SimpleNamespace(),
        _emit=lambda *_args, **_kwargs: None,
        _task_failed=False,
        _cancelled=False,
    )

    class _Executor:
        def __init__(self) -> None:
            self.orchestrator = orchestrator

        def _rebuild_dependency_map(self) -> None:
            return None

    assert run_plan_loop(_Executor(), objective, {}, False) is None
    assert state.terminal_disposition == "complete"


def test_boundary_extension_with_no_persisted_steps_blocks_instead_of_succeeding() -> None:
    state = AgentState()
    objective = "explique o resultado"
    initialize_task_progression(SimpleNamespace(agent_state=state), objective)
    state.record_tool_result(
        "file_reader",
        {"file_path": "a.txt"},
        {"ok": True, "done": True, "status": "succeeded", "data": "x"},
    )
    orchestrator = SimpleNamespace(
        agent_state=state,
        session=SimpleNamespace(config={}),
        plan_builder=SimpleNamespace(
            continue_after_reasoning_boundary=lambda _objective: PlanBuildResult(
                plan=[{"tool": "grep", "args": {}}],
            )
        ),
        execution_gateway=SimpleNamespace(
            extend_validated_plan=lambda _plan, _objective: [{"tool": "grep", "args": {}}],
        ),
        _emit=lambda *_args, **_kwargs: None,
        _task_failed=False,
        _cancelled=False,
    )

    class _Executor:
        def __init__(self) -> None:
            self.orchestrator = orchestrator

        def _rebuild_dependency_map(self) -> None:
            return None

    answer = run_plan_loop(_Executor(), objective, {}, False)

    assert answer is not None
    assert state.terminal_disposition == "block"


def test_reasoning_policy_has_no_completion_import_cycle() -> None:
    source = Path("agent/planning/reasoning_boundary.py").read_text(encoding="utf-8")

    assert "task_completion" not in source


def _failure_runtime_orchestrator(state: AgentState, *, task_failed: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        agent_state=state,
        tool_registry=None,
        _task_failed=task_failed,
        _cancelled=False,
        _emit=lambda *_args, **_kwargs: None,
    )


def test_production_canonical_review_amendment_blocks_same_completion() -> None:
    state = AgentState()
    objective = "Explique a situacao."
    initialize_task_progression(SimpleNamespace(agent_state=state), objective)
    state.record_tool_result(
        "echo",
        {},
        {"ok": True, "done": True, "executed": True, "status": "succeeded", "data": "observado"},
    )

    class _Context:
        calls = 0

        @staticmethod
        def ask_model(*_args, **_kwargs):
            _Context.calls += 1
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

    events = []
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

    assert _Context.calls == 1
    assert result.completed is True
    assert result.answer is None
    assert state.task_obligations == ()
    assert state.terminal_disposition == "complete"
    assert ("canonical_review_amendment", {"added": 0}) in events


def test_canonical_review_status_payload_is_rejected_without_mutation() -> None:
    state = AgentState()
    objective = "Explique a situacao."
    initialize_task_progression(SimpleNamespace(agent_state=state), objective)
    state.record_tool_result(
        "echo",
        {},
        {"ok": True, "done": True, "executed": True, "status": "succeeded", "data": "observado"},
    )

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
                        "status": "satisfied",
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
        _emit=lambda *_args, **_kwargs: None,
        _log_metric=lambda *_args, **_kwargs: None,
        _build_tools_description=lambda **_kwargs: "echo(...); file_reader(...)",
    )
    orchestrator.plan_builder = PlanBuilder(orchestrator)

    result = continue_after_reasoning_boundary(orchestrator, objective)

    assert result.completed is True
    assert state.task_obligations == ()
    assert state.terminal_disposition == "complete"


def test_d3_runtime_compare_of_two_complete_empty_files_succeeds() -> None:
    state = AgentState()
    objective = "Compare a.txt e b.txt e me diga se o conteudo deles e igual. Nao altere nada."
    initialize_task_progression(SimpleNamespace(agent_state=state), objective)
    state.record_tool_result(
        "file_reader",
        {"file_path": "a.txt"},
        {
            "ok": True,
            "done": True,
            "executed": True,
            "status": "succeeded",
            "data": "",
            "complete": True,
            "invocation_id": "read-a",
        },
    )
    state.record_tool_result(
        "file_reader",
        {"file_path": "b.txt"},
        {
            "ok": True,
            "done": True,
            "executed": True,
            "status": "succeeded",
            "data": "",
            "complete": True,
            "invocation_id": "read-b",
        },
    )
    orchestrator = _failure_runtime_orchestrator(state, task_failed=False)

    assert allow_linear_completion(orchestrator, objective) is None
    outcome = project_operational_outcome(state)
    answer = compose_operational_answer(
        outcome,
        "O conteudo de a.txt e b.txt e igual.",
        state.tool_history,
    )

    assert state.terminal_disposition == "complete"
    assert state.obligation_status("read:1").value == "satisfied"
    assert state.obligation_status("read:2").value == "satisfied"
    assert state.task_semantics.obligation_evidence("requirement:compare") == (1, 2)
    assert outcome.terminal_status == "succeeded"
    assert outcome.mutation_occurred is False
    assert "igual" in answer.casefold()


def test_d4_zero_match_search_reaches_canonical_success() -> None:
    state = AgentState()
    objective = "Procure X nos arquivos."
    initialize_task_progression(SimpleNamespace(agent_state=state), objective)
    state.record_tool_result(
        "grep",
        {"path": ".", "pattern": "X"},
        {
            "ok": True,
            "done": True,
            "executed": True,
            "status": "succeeded",
            "complete": True,
            "data": [],
            "total_matches": 0,
            "invocation_id": "grep-zero",
        },
    )

    assert allow_linear_completion(_failure_runtime_orchestrator(state, task_failed=False), objective) is None
    assert state.terminal_disposition == "complete"
    assert state.obligation_status("requirement:search").value == "satisfied"
    assert state.task_semantics.obligation_evidence("requirement:search") == (1,)


def test_d5_missing_file_without_fallback_is_canonical_non_success() -> None:
    state = AgentState()
    objective = "Leia missing.txt e informe o conteudo."
    initialize_task_progression(SimpleNamespace(agent_state=state), objective)
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
    orchestrator = _failure_runtime_orchestrator(state)

    answer = allow_linear_completion(orchestrator, objective)

    assert answer is not None
    assert state.terminal_disposition == "fail"
    assert state.pending_obligations()
    assert state.last_result["status"] == "failed"
    assert "conteudo" not in answer.casefold()


def test_d6_success_plus_failed_read_and_exact_fallback_can_succeed() -> None:
    state = AgentState()
    objective = "Leia controle.txt e missing.txt e identifique o motivo se um arquivo nao puder ser lido."
    initialize_task_progression(SimpleNamespace(agent_state=state), objective)
    state.review_task_obligations(
        [
            {
                "id": "fallback:missing",
                "kind": "fallback",
                "fallback_target": "missing.txt",
                "description": "Identificar o motivo se missing.txt nao puder ser lido.",
            }
        ],
        source="canonical_review",
    )
    state.record_tool_result(
        "file_reader",
        {"file_path": "controle.txt"},
        {
            "ok": True,
            "done": True,
            "executed": True,
            "status": "succeeded",
            "data": "modificado",
            "complete": True,
            "invocation_id": "controle-1",
        },
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
    orchestrator = _failure_runtime_orchestrator(state)

    assert allow_linear_completion(orchestrator, objective) is None
    assert state.terminal_disposition == "complete"
    assert state.obligation_status("fallback:missing").value == "satisfied"
    assert state.obligation_status("read:2").value == "waived"
    assert state.tool_history[1]["result"]["status"] == "failed"

    outcome = project_operational_outcome(state, task_failed=True)
    receipt = build_run_receipt(Path("."), state, "succeeded", None)
    answer = compose_operational_answer(
        outcome,
        "controle.txt: modificado",
        state.tool_history,
    )

    assert outcome.terminal_status == "succeeded"
    assert outcome.failed_invocation_ids == ("missing-1",)
    assert receipt["tools"][1]["status"] == "failed"
    assert receipt["operational_outcome"]["failed_invocation_ids"] == ["missing-1"]
    assert "modificado" in answer
    assert "missing-1" in answer


def test_fallback_subject_mismatch_cannot_soften_local_failure() -> None:
    state = AgentState()
    objective = "Leia missing.txt e identifique o motivo se ele nao puder ser lido."
    initialize_task_progression(SimpleNamespace(agent_state=state), objective)
    state.review_task_obligations(
        [
            {
                "id": "fallback:other",
                "kind": "fallback",
                "fallback_target": "other.txt",
                "description": "Identificar o motivo se other.txt nao puder ser lido.",
            }
        ],
        source="canonical_review",
    )
    state.record_tool_result(
        "file_reader",
        {"file_path": "missing.txt"},
        {"ok": False, "status": "failed", "error": "missing", "invocation_id": "missing-2"},
    )

    answer = allow_linear_completion(_failure_runtime_orchestrator(state), objective)

    assert answer is not None
    assert state.terminal_disposition == "fail"
    assert state.obligation_status("fallback:other").value == "pending"


@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        ("permission_denied", "PERMISSION_DENIED"),
        ("blocked", "SAFETY_BLOCK"),
        ("failed", "TASK_AUTHORITY_DENIED"),
        ("protocol_error", "INVALID_RESULT"),
        ("unverified", "unverified"),
    ],
)
def test_hard_boundaries_cannot_be_softened_by_matching_fallback(
    status: str,
    error_code: str,
) -> None:
    state = AgentState()
    objective = "Leia missing.txt e identifique o motivo se ele nao puder ser lido."
    initialize_task_progression(SimpleNamespace(agent_state=state), objective)
    state.review_task_obligations(
        [
            {
                "id": "fallback:missing",
                "kind": "fallback",
                "fallback_target": "missing.txt",
                "description": "Identificar o motivo se missing.txt nao puder ser lido.",
            }
        ],
        source="canonical_review",
    )
    state.record_tool_result(
        "file_reader",
        {"file_path": "missing.txt"},
        {
            "ok": False,
            "done": True,
            "executed": False,
            "status": status,
            "error": "hard boundary",
            "error_code": error_code,
            "invocation_id": f"hard-{status}",
        },
    )

    answer = allow_linear_completion(_failure_runtime_orchestrator(state), objective)

    assert answer is not None
    assert state.terminal_disposition != "complete"
    assert state.obligation_status("fallback:missing").value == "pending"
