from __future__ import annotations

import copy

import pytest

from agent.planning.task_semantics import (
    ObligationStatus,
    TaskIntent,
    TaskObligation,
    TaskSemantics,
)
from agent.state import AgentState


class _Memory:
    def __init__(self) -> None:
        self.state = {}


def _state() -> AgentState:
    return AgentState(memory=_Memory())


def _complete(data: str) -> dict[str, object]:
    return {
        "ok": True,
        "done": True,
        "executed": True,
        "status": "succeeded",
        "complete": True,
        "data": data,
    }


def _canonical_read_checkpoint() -> dict[str, object]:
    state = _state()
    state.initialize_task_semantics("Leia b.txt.")
    state.record_tool_result(
        "file_reader",
        {"file_path": "b.txt"},
        _complete("B"),
    )
    return state.to_checkpoint_dict()


def test_checkpoint_restore_revalidates_valid_terminal_evidence() -> None:
    checkpoint = _canonical_read_checkpoint()

    restored = _state()
    restored.from_checkpoint_dict(checkpoint)

    assert restored.obligation_status("requirement:read") is ObligationStatus.SATISFIED
    assert restored.task_semantics.obligation_evidence("requirement:read") == (1,)
    assert restored.terminal_evidence_complete() is True


def test_checkpoint_restore_rejects_terminal_evidence_ref_missing_from_history() -> None:
    checkpoint = copy.deepcopy(_canonical_read_checkpoint())
    semantics = checkpoint["task_semantics"]
    assert isinstance(semantics, dict)
    evidence = semantics["evidence"]
    assert isinstance(evidence, dict)
    evidence["requirement:read"] = [999]

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
