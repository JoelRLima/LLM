from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.execution_state import StepExecutionRecord, StepStatus
from agent.planning.plan_builder import PlanBuildResult, PlanningDecisionKind
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
)
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
