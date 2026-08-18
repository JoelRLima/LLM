import json

from agent.execution_state import StepStatus
from agent.runtime.budget import TaskBudgetLedger
from agent.state import AgentState


class _Memory:
    def __init__(self):
        self.state = {}


def _state(monkeypatch):
    monkeypatch.setattr("agent.state.AgentMemory", _Memory)
    return AgentState()


def test_plan_steps_have_stable_ids_and_explicit_transitions(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan([{"tool": "echo", "args": {"text": "a"}}])
    step_id = state.get_step_id(0)

    state.mark_step_running(0)
    assert state.get_step_status(0) is StepStatus.RUNNING
    assert state.step_records[step_id].attempts == 1

    state.mark_step_completed(0)
    state.set_plan(state.plan)

    assert state.get_step_id(0) == step_id
    assert state.get_step_status(0) is StepStatus.COMPLETED
    assert state.next_pending_index() is None


def test_checkpoint_resume_requeues_running_but_preserves_completed(monkeypatch):
    state = _state(monkeypatch)
    state.objective = "continuar"
    state.set_plan(
        [
            {"tool": "echo", "args": {"text": "feito"}},
            {"tool": "echo", "args": {"text": "interrompido"}},
        ]
    )
    state.mark_step_running(0)
    state.mark_step_completed(0)
    state.mark_step_running(1)

    restored = _state(monkeypatch)
    restored.from_checkpoint_dict(state.to_checkpoint_dict())

    assert restored.get_step_status(0) is StepStatus.COMPLETED
    assert restored.get_step_status(1) is StepStatus.PENDING
    assert restored.next_pending_index() == 1


def test_checkpoint_preserves_deferred_condition_without_tool_args(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {
                "tool": "file_reader",
                "args": {"file_path": "controle.txt"},
                "_step_id": "observation-step",
            },
            {
                "kind": "deferred_condition",
                "observation_ref": "observation-step",
                "predicate": {"op": "equals", "value": "original"},
                "on_true": {"tool": "echo", "args": {"text": "modificado"}},
                "on_false": {"waive_effect": "write"},
            },
        ]
    )
    state.mark_step_completed(0)

    restored = _state(monkeypatch)
    restored.from_checkpoint_dict(state.to_checkpoint_dict())

    assert restored.plan[1]["kind"] == "deferred_condition"
    assert "args" not in restored.plan[1]
    assert restored.plan[1]["observation_ref"] == "observation-step"
    assert restored.get_step_status(0) is StepStatus.COMPLETED
    assert restored.get_step_status(1) is StepStatus.PENDING


def test_checkpoint_preserves_persona_and_prompt(monkeypatch):
    state = _state(monkeypatch)
    state.objective = "continuar"
    state.persona = "coder"
    state.persona_prompt = "You are a coder persona"
    checkpoint = state.to_checkpoint_dict()

    restored = _state(monkeypatch)
    restored.from_checkpoint_dict(checkpoint)

    assert restored.persona == "coder"
    assert restored.persona_prompt == "You are a coder persona"


def test_checkpoint_preserves_canonical_task_progression(monkeypatch):
    state = _state(monkeypatch)
    state.reset_task_progression(["write"])
    state.record_executed_effect("write")
    state.continuation_attempts = 1
    state.terminal_disposition = "complete"

    restored = _state(monkeypatch)
    restored.from_checkpoint_dict(state.to_checkpoint_dict())

    assert restored.requested_effects == ["write"]
    assert restored.executed_effects == ["write"]
    assert restored.pending_effects() == ()
    assert restored.continuation_attempts == 1
    assert restored.terminal_disposition == "complete"


def test_replan_replaces_step_and_its_execution_record(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan([{"tool": "missing", "args": {}}])
    old_id = state.get_step_id(0)

    state.replace_plan_step(0, [{"tool": "echo", "args": {}}])

    assert old_id not in state.step_records
    assert state.get_step_id(0) != old_id
    assert state.get_step_status(0) is StepStatus.PENDING


def test_resume_retry_policy_is_opt_in_for_terminal_failures(monkeypatch):
    state = _state(monkeypatch)
    state.set_plan(
        [
            {"tool": "echo", "args": {"text": "falhou"}},
            {"tool": "echo", "args": {"text": "pulado"}},
        ]
    )
    state.mark_step_failed(0, "erro")
    state.mark_step_skipped(1, "dependência")
    checkpoint = state.to_checkpoint_dict()

    conservative = _state(monkeypatch)
    conservative.from_checkpoint_dict(checkpoint)
    assert conservative.next_pending_index() is None

    retrying = _state(monkeypatch)
    retrying.from_checkpoint_dict(
        checkpoint, retry_failed=True, retry_skipped=True
    )
    assert retrying.get_step_status(0) is StepStatus.PENDING
    assert retrying.get_step_status(1) is StepStatus.PENDING


def test_checkpoint_preserves_task_budget_snapshot(monkeypatch):
    ledger = TaskBudgetLedger(max_model_calls=3, max_task_tool_calls=3)
    call_number = ledger.reserve_model_call()
    ledger.finalize_model_call(
        call_number,
        usage={"input_tokens": 4, "output_tokens": 6},
    )
    ledger.reserve_tool_call()
    state = AgentState(memory=_Memory(), budget_ledger=ledger)
    state.objective = "continuar"

    checkpoint = state.to_checkpoint_dict()
    json.dumps(checkpoint)

    restored_ledger = TaskBudgetLedger(max_model_calls=3, max_task_tool_calls=3)
    restored = AgentState(memory=_Memory(), budget_ledger=restored_ledger)
    restored.from_checkpoint_dict(checkpoint)

    assert restored_ledger.snapshot() == ledger.snapshot()
    assert restored_ledger.reserve_tool_call() == 2
