from agent.execution_state import StepStatus
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
