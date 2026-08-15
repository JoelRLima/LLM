from types import SimpleNamespace

import pytest

from agent.execution_state import StepExecutionRecord, StepStatus
from agent.planning.task_completion import allow_linear_completion, refresh_executed_effects
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
