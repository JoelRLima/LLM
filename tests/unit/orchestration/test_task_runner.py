from types import SimpleNamespace

import pytest

from agent.memory.json_persistence import AtomicJsonWriteError
from agent.orchestration import task_runner as task_runner_module
from agent.orchestration.task_runner import TaskInputs, TaskRunner
from agent.planning.plan_builder import PlanBuildResult, PlanningDecisionKind
from agent.runtime.budget import TaskBudgetLedger
from agent.state import AgentState


class _CancellationToken:
    def reset(self) -> None:
        pass

    def cancel(self) -> None:
        pass


class _FailingPersistenceOrchestrator:
    def __init__(self, memory_path) -> None:
        self.session = SimpleNamespace(messages=[], config={})
        self.agent_state = SimpleNamespace(
            max_history_turns=5,
            conversation_history=[],
        )
        self.cancellation_token = _CancellationToken()
        self.workspace = SimpleNamespace(rollback=lambda: None)
        self.context_manager = SimpleNamespace(maybe_compress_context=lambda: None)
        self._task_failed = False
        self._cancelled = False
        self.persistence_calls = 0
        self.checkpoint_deleted = False
        self.memory_path = memory_path

    def _reset_task_state(self, objective: str) -> None:
        self.objective = objective
        self._task_failed = False

    def _count_metrics_lines(self) -> int:
        return 0

    def _answer_trivial(self, objective: str) -> str:
        return f"resposta para {objective}"

    def _persist_memory_to_file(self) -> None:
        self.persistence_calls += 1
        raise AtomicJsonWriteError(
            self.memory_path,
            OSError("disco indisponível"),
        )

    def _delete_checkpoint(self) -> None:
        self.checkpoint_deleted = True


def test_task_success_is_not_returned_when_automatic_memory_save_fails(
    tmp_path,
) -> None:
    orchestrator = _FailingPersistenceOrchestrator(
        tmp_path / "agent_memory.json"
    )

    with pytest.raises(AtomicJsonWriteError, match="disco indisponível"):
        TaskRunner(orchestrator).run("oi", None)

    assert orchestrator.persistence_calls == 1
    assert orchestrator._task_failed is True
    assert orchestrator.checkpoint_deleted is False


def test_initial_planner_block_uses_canonical_completion_owner(monkeypatch) -> None:
    state = AgentState()
    completion_calls = []

    def completion_owner(orchestrator, objective):
        completion_calls.append((orchestrator, objective))
        orchestrator.agent_state.terminal_disposition = "block"
        return "planejamento bloqueado"

    monkeypatch.setattr(task_runner_module, "allow_linear_completion", completion_owner)
    orchestrator = SimpleNamespace(
        agent_state=state,
        plan_builder=SimpleNamespace(
            build_plan=lambda _objective: PlanBuildResult(
                blocked_answer="planejamento bloqueado",
                kind=PlanningDecisionKind.BLOCK,
            )
        ),
        session=SimpleNamespace(config={}),
        _task_failed=False,
        _cancelled=False,
        _route_persona=lambda _objective: None,
        _save_checkpoint=lambda: None,
        _is_security_objective=lambda _objective: False,
        _try_hierarchical=lambda *_args: None,
        _try_security=lambda *_args: None,
        _emit=lambda *_args, **_kwargs: None,
        tool_registry=None,
    )

    answer = TaskRunner(orchestrator)._execute(TaskInputs("objetivo", False, 0), None)

    assert answer == "planejamento bloqueado"
    assert completion_calls == [(orchestrator, "objetivo")]
    assert state.terminal_disposition == "block"
    assert state.last_result["status"] == "blocked"


def test_new_task_boundary_resets_shared_ledger_once() -> None:
    class CountingLedger(TaskBudgetLedger):
        reset_calls = 0

        def reset(self) -> None:
            self.reset_calls += 1
            super().reset()

    ledger = CountingLedger()
    call_number = ledger.reserve_model_call()
    ledger.finalize_model_call(call_number, estimated_tokens=7)
    ledger.reserve_tool_call()
    state = AgentState()
    orchestrator = SimpleNamespace(
        task_budget=ledger,
        agent_state=state,
        context_manager=SimpleNamespace(_cached_project_context="cached"),
        workspace=SimpleNamespace(restore_points={"restore": object()}),
        _planning_context="planning",
        _task_failed=True,
        cancellation_token=_CancellationToken(),
    )

    from agent.orchestrator import Orchestrator

    Orchestrator._reset_task_state(orchestrator, "task B")

    assert ledger.reset_calls == 1
    assert ledger.snapshot().model_calls == 0
    assert ledger.snapshot().tool_calls == 0
    assert state.objective == "task B"


def test_resume_prepare_does_not_reset_shared_ledger() -> None:
    class CountingLedger(TaskBudgetLedger):
        reset_calls = 0

        def reset(self) -> None:
            self.reset_calls += 1
            super().reset()

    ledger = CountingLedger()
    call_number = ledger.reserve_model_call()
    ledger.finalize_model_call(call_number, estimated_tokens=7)
    orchestrator = SimpleNamespace(
        task_budget=ledger,
        _task_failed=True,
        _task_start_time=0.0,
        _run_id=None,
        _run_metric_recorded=False,
        _metrics_start_line=0,
        _count_metrics_lines=lambda: 0,
    )

    TaskRunner(orchestrator)._prepare(TaskInputs("resumed", True, 0))

    assert ledger.reset_calls == 0
    assert ledger.snapshot().accounted_tokens == 7
