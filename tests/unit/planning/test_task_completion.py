from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.execution_state import StepExecutionRecord, StepStatus
from agent.planning.plan_builder import PlanBuildResult, PlanningDecisionKind
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


def test_reasoning_policy_has_no_completion_import_cycle() -> None:
    source = Path("agent/planning/reasoning_boundary.py").read_text(encoding="utf-8")

    assert "task_completion" not in source
