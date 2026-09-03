from types import SimpleNamespace

import pytest

from agent.cancellation import CancellationToken
from agent.orchestration.task_lifecycle import TaskLifecycleMixin
from agent.runtime.correlation import RunCorrelation
from agent.state import AgentState


class _InvocationGateway:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def request_invocation_cancellation(self) -> None:
        self.calls.append("request_cancel")

    def drain_invocations(self, *, timeout_seconds: float) -> bool:
        self.calls.append(("drain", timeout_seconds))
        return True


class _Policy:
    def __init__(self, calls: list[object]) -> None:
        self.calls = calls

    def pause_active_segment(self) -> None:
        self.calls.append("pause_policy")


def _lifecycle_for(orchestrator: object) -> TaskLifecycleMixin:
    lifecycle = TaskLifecycleMixin()
    lifecycle.orchestrator = orchestrator
    return lifecycle


def test_keyboard_interrupt_pauses_and_saves_non_terminal_state() -> None:
    state = AgentState(root_task_id="root-task")
    correlation = RunCorrelation.fresh(root_task_id="root-task")
    state.runtime_correlation = correlation
    calls: list[object] = []
    gateway = _InvocationGateway()
    saved: list[dict[str, object]] = []

    def save_checkpoint() -> bool:
        calls.append("save_checkpoint")
        saved.append(state.to_checkpoint_dict())
        return True

    orchestrator = SimpleNamespace(
        agent_state=state,
        cancellation_token=CancellationToken(),
        task_policy=_Policy(calls),
        tool_invocation_gateway=gateway,
        _preserve_checkpoint=False,
        _save_checkpoint=save_checkpoint,
    )

    answer = _lifecycle_for(orchestrator)._handle_interrupt()

    assert "pausada" in answer
    assert orchestrator.cancellation_token.cancelled is True
    assert gateway.calls == ["request_cancel", ("drain", 5.0)]
    assert calls == ["pause_policy", "save_checkpoint"]
    assert state.terminal_disposition is None
    assert orchestrator._preserve_checkpoint is True
    assert state.continuity == {
        "schema_version": 1,
        "resume_generation": 0,
        "last_run_id": correlation.run_id,
        "interrupted": True,
        "interruption_reason": "keyboard_interrupt",
        "interrupted_at": state.continuity["interrupted_at"],
    }
    assert saved[0]["continuity"] == state.continuity
    assert "prompt" not in saved[0]["continuity"]
    assert "authority" not in saved[0]["continuity"]
    assert "pid" not in saved[0]["continuity"]
    assert "trace_path" not in saved[0]["continuity"]


def test_pause_only_cleanup_preserves_non_terminal_progress_without_rollback() -> None:
    state = AgentState(root_task_id="root-task")
    state.record_continuity_interruption(interrupted_at="2026-09-02T12:00:00Z")
    rollback_calls: list[bool] = []
    orchestrator = SimpleNamespace(
        agent_state=state,
        task_policy=SimpleNamespace(pause_active_segment=lambda: None),
        tool_invocation_gateway=None,
        _task_failed=False,
        _cancelled=False,
        _preserve_checkpoint=True,
        workspace=SimpleNamespace(
            restore_points={"mutation": object()},
            rollback=lambda: rollback_calls.append(True),
        ),
        session=SimpleNamespace(messages=[]),
        context_manager=SimpleNamespace(maybe_compress_context=lambda: None),
        _persist_memory_to_file=lambda: None,
    )

    _lifecycle_for(orchestrator)._cleanup(0)

    assert state.terminal_disposition is None
    assert state.continuity["interrupted"] is True
    assert rollback_calls == []


def test_interrupted_hard_failure_still_uses_rollback_failure_cleanup() -> None:
    state = AgentState(root_task_id="root-task")
    state.record_continuity_interruption(interrupted_at="2026-09-02T12:00:00Z")
    rollback_calls: list[bool] = []
    deleted: list[bool] = []
    orchestrator = SimpleNamespace(
        agent_state=state,
        task_policy=SimpleNamespace(pause_active_segment=lambda: None),
        tool_invocation_gateway=None,
        _task_failed=True,
        _cancelled=False,
        _preserve_checkpoint=True,
        workspace=SimpleNamespace(
            restore_points={"mutation": object()},
            rollback=lambda: rollback_calls.append(True) or False,
        ),
        session=SimpleNamespace(messages=[]),
        context_manager=SimpleNamespace(maybe_compress_context=lambda: None),
        _persist_memory_to_file=lambda: None,
        _delete_checkpoint=lambda: deleted.append(True),
    )

    _lifecycle_for(orchestrator)._cleanup(0)

    assert rollback_calls == [True]
    assert state.terminal_disposition == "fail"
    assert state.last_result["error_code"] == "TASK_CLEANUP_FAILURE"
    assert orchestrator._preserve_checkpoint is False
    assert deleted == [True]


def test_continuity_round_trip_advances_generation_on_supported_resume() -> None:
    state = AgentState(root_task_id="root-task")
    state.objective = "continuar tarefa"
    first = RunCorrelation.fresh(root_task_id="root-task")
    state.runtime_correlation = first
    state.record_continuity_interruption(interrupted_at="2026-09-02T12:00:00Z")
    checkpoint = state.to_checkpoint_dict()

    restored = AgentState()
    restored.from_checkpoint_dict(checkpoint)

    assert restored.continuity == checkpoint["continuity"]
    assert restored.root_task_id == "root-task"
    assert restored.terminal_disposition is None

    resumed = RunCorrelation.resume(restored.root_task_id)
    restored.runtime_correlation = resumed

    assert restored.continuity == {
        "schema_version": 1,
        "resume_generation": 1,
        "last_run_id": resumed.run_id,
        "resumed_from_run_id": first.run_id,
        "interrupted": False,
        "interruption_reason": None,
        "interrupted_at": None,
    }


def test_schema_2_checkpoint_without_continuity_remains_restorable() -> None:
    state = AgentState()
    state.objective = "legacy checkpoint"
    checkpoint = state.to_checkpoint_dict()
    assert "continuity" not in checkpoint

    restored = AgentState()
    restored.from_checkpoint_dict(checkpoint)

    assert restored.continuity is None


@pytest.mark.parametrize(
    "continuity",
    [
        {
            "schema_version": 1,
            "resume_generation": 0,
            "last_run_id": None,
            "interrupted": False,
            "interruption_reason": "stale",
        },
        {
            "schema_version": 1,
            "resume_generation": 0,
            "last_run_id": None,
            "interrupted": False,
            "prompt": "raw prompt",
        },
    ],
)
def test_continuity_metadata_is_validated_during_restore(continuity: dict[str, object]) -> None:
    state = AgentState()
    state.objective = "invalid continuity"
    checkpoint = state.to_checkpoint_dict()
    checkpoint["continuity"] = continuity

    with pytest.raises(ValueError, match="continuity"):
        AgentState().from_checkpoint_dict(checkpoint)
