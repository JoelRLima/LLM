from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.checkpoint_manager import CheckpointLoadError
from agent.memory.json_persistence import AtomicJsonWriteError
from agent.orchestration import task_runner as task_runner_module
from agent.orchestration.route_result import RouteDisposition, RouteResult
from agent.orchestration.task_runner import TaskInputs, TaskRunner
from agent.planning.plan_builder import PlanBuildResult, PlanningDecisionKind
from agent.runtime.budget import BudgetExhausted, TaskBudgetLedger
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


def test_planner_block_without_explanation_does_not_fall_through_to_reactive() -> None:
    state = AgentState()
    reactive_calls = []
    orchestrator = SimpleNamespace(
        agent_state=state,
        plan_builder=SimpleNamespace(
            build_plan=lambda _objective: PlanBuildResult(
                blocked_answer=None,
                kind=PlanningDecisionKind.BLOCK,
            )
        ),
        session=SimpleNamespace(config={}),
        _task_failed=False,
        _cancelled=False,
        _route_persona=lambda _objective: None,
        _save_checkpoint=lambda: None,
        _is_security_objective=lambda _objective: False,
        _run_reactive=lambda *_args: reactive_calls.append(True),
        _emit=lambda *_args, **_kwargs: None,
        tool_registry=None,
    )

    answer = TaskRunner(orchestrator)._execute(TaskInputs("objetivo", False, 0), None)

    assert "planejamento bloqueou" in answer
    assert reactive_calls == []
    assert state.terminal_disposition == "block"
    assert state.last_result["status"] == "blocked"


def test_terminal_cancelled_checkpoint_resumes_without_starting_a_route() -> None:
    checkpoint_state = AgentState()
    checkpoint_state.objective = "tarefa cancelada"
    checkpoint_state.terminal_disposition = "cancelled"
    checkpoint_state.last_result = {
        "ok": False,
        "status": "cancelled",
        "error_code": "CANCELLED",
        "message": "Tarefa cancelada pelo usuario.",
    }
    checkpoint = checkpoint_state.to_checkpoint_dict()
    restored = AgentState()
    executed = []
    deleted = []
    orchestrator = SimpleNamespace(
        session=SimpleNamespace(messages=[], config={}),
        agent_state=restored,
        cancellation_token=_CancellationToken(),
        _load_checkpoint=lambda: checkpoint,
        _delete_checkpoint=lambda: deleted.append(True),
        _save_checkpoint=lambda: None,
        _persist_memory_to_file=lambda: None,
        _task_failed=False,
        _cancelled=False,
        _preserve_checkpoint=False,
        workspace=SimpleNamespace(rollback=lambda: None),
        context_manager=SimpleNamespace(maybe_compress_context=lambda: None),
        _emit=lambda event_type, data=None: restored.events.append(
            {"type": event_type, "data": data or {}}
        ),
    )
    runner = TaskRunner(orchestrator)
    runner._execute = lambda *_args, **_kwargs: executed.append(True)

    answer = runner.run(None, None)

    assert answer == "Tarefa cancelada pelo usuario."
    assert executed == []
    assert deleted == []
    assert restored.terminal_disposition == "cancelled"
    assert orchestrator._preserve_checkpoint is True


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


def test_new_task_boundary_clears_terminal_and_cancellation_state() -> None:
    from agent.orchestrator import Orchestrator

    state = AgentState()
    state.terminal_disposition = "complete"
    orchestrator = SimpleNamespace(
        task_budget=TaskBudgetLedger(),
        agent_state=state,
        context_manager=SimpleNamespace(_cached_project_context="cached"),
        workspace=SimpleNamespace(restore_points={}),
        _planning_context="planning",
        _task_failed=True,
        _cancelled=True,
        cancellation_token=_CancellationToken(),
    )

    Orchestrator._reset_task_state(orchestrator, "task C")

    assert state.terminal_disposition is None
    assert orchestrator._cancelled is False


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


def _route_orchestrator(state: AgentState | None = None):
    events = []
    orchestrator = SimpleNamespace(
        agent_state=state or AgentState(),
        _task_failed=False,
        _cancelled=False,
        _emit=lambda event_type, data=None: events.append(
            {"type": event_type, "data": data or {}}
        ),
        tool_registry=None,
    )
    return orchestrator, events


def test_route_selector_returns_typed_not_applicable_without_calling_handler(monkeypatch) -> None:
    orchestrator, _ = _route_orchestrator()
    called = []
    orchestrator._run_hierarchical = lambda *_args: called.append(True)
    monkeypatch.setattr(task_runner_module, "is_hierarchical", lambda _objective: False)

    result = TaskRunner(orchestrator)._try_hierarchical("objetivo", None)

    assert isinstance(result, RouteResult)
    assert result.disposition is RouteDisposition.NOT_APPLICABLE
    assert result.route == "hierarchical"
    assert called == []
    assert orchestrator.agent_state.terminal_disposition is None


def test_allowed_fallback_emits_transition_and_preserves_state() -> None:
    from agent.runtime.budget import TaskBudgetLedger

    ledger = TaskBudgetLedger()
    ledger.reserve_tool_call()
    state = AgentState(budget_ledger=ledger)
    orchestrator, events = _route_orchestrator(state)
    before = ledger.snapshot()

    answer = TaskRunner(orchestrator)._consume_route_result(
        RouteResult.fallback(
            "hierarchical",
            reason_code="HIERARCHICAL_PLANNER_ERROR",
            detail="safe detail",
        ),
        "objetivo",
        next_route="security",
    )

    after = ledger.snapshot()
    assert answer is None
    assert state.terminal_disposition is None
    assert after == before
    assert events == [
        {
            "type": "route_transition",
            "data": {
                "route": "hierarchical",
                "disposition": "fallback",
                "reason_code": "HIERARCHICAL_PLANNER_ERROR",
                "next_route": "security",
                "action": "continue",
            },
        }
    ]


def test_handled_route_without_terminal_truth_fails_closed() -> None:
    orchestrator, events = _route_orchestrator()

    answer = TaskRunner(orchestrator)._consume_route_result(
        RouteResult.handled("hierarchical", answer="apparently complete"),
        "objetivo",
        next_route="security",
    )

    assert "estado terminal canonico" in answer
    assert orchestrator.agent_state.terminal_disposition == "unverified"
    assert orchestrator.agent_state.last_result["status"] == "unverified"
    assert events[-1]["type"] == "task_outcome"


def test_security_analyzer_fallback_stops_and_records_reason() -> None:
    orchestrator, events = _route_orchestrator()

    answer = TaskRunner(orchestrator)._consume_route_result(
        RouteResult.fallback("security", reason_code="SECURITY_ANALYZER_DENIED"),
        "audite app.py",
        next_route="linear",
    )

    assert answer
    assert orchestrator.agent_state.terminal_disposition == "permission_denied"
    transition = next(event for event in events if event["type"] == "route_transition")
    assert transition["data"] == {
        "route": "security",
        "disposition": "fallback",
        "reason_code": "SECURITY_ANALYZER_DENIED",
        "next_route": None,
        "action": "stop",
    }


def test_route_boundary_rejects_legacy_none_as_unverified(monkeypatch) -> None:
    orchestrator, _ = _route_orchestrator()
    orchestrator._run_hierarchical = lambda *_args: None
    monkeypatch.setattr(task_runner_module, "is_hierarchical", lambda _objective: True)

    result = TaskRunner(orchestrator)._try_hierarchical("objetivo", None)

    assert result.disposition is RouteDisposition.HANDLED
    assert result.reason_code == "ROUTE_RESULT_CONTRACT_VIOLATION"
    assert orchestrator.agent_state.terminal_disposition == "unverified"


def test_fallback_policy_allowlist_rejects_unsafe_route_recovery() -> None:
    assert TaskRunner._fallback_allowed(
        "hierarchical", "HIERARCHICAL_PLANNER_ERROR", "security"
    ) is True
    assert TaskRunner._fallback_allowed(
        "hierarchical", "HIERARCHICAL_PRECONDITION_UNAVAILABLE", "security"
    ) is False
    assert TaskRunner._fallback_allowed(
        "security", "SECURITY_TARGET_UNAVAILABLE", "linear"
    ) is True
    assert TaskRunner._fallback_allowed(
        "security", "SECURITY_GATEWAY_UNAVAILABLE", "linear"
    ) is False
    assert TaskRunner._fallback_allowed(
        "security", "SECURITY_ANALYZER_DENIED", "linear"
    ) is False


def test_security_infrastructure_fallback_is_terminal_unavailable() -> None:
    orchestrator, events = _route_orchestrator()

    answer = TaskRunner(orchestrator)._consume_route_result(
        RouteResult.fallback("security", reason_code="SECURITY_GATEWAY_UNAVAILABLE"),
        "audite app.py",
        next_route="linear",
    )

    assert answer
    assert orchestrator.agent_state.terminal_disposition == "unavailable"
    assert orchestrator.agent_state.last_result["error_code"] == "SECURITY_GATEWAY_UNAVAILABLE"
    transition = next(event for event in events if event["type"] == "route_transition")
    assert transition["data"]["action"] == "stop"


def test_budget_exhaustion_becomes_blocked_and_keeps_checkpoint() -> None:
    state = AgentState()
    checkpoint_saves = []
    deleted = []
    orchestrator = SimpleNamespace(
        session=SimpleNamespace(messages=[], config={}),
        agent_state=state,
        cancellation_token=_CancellationToken(),
        _cancelled=False,
        _task_failed=False,
        workspace=SimpleNamespace(rollback=lambda: None),
        context_manager=SimpleNamespace(maybe_compress_context=lambda: None),
        _emit=lambda event_type, data=None: state.events.append(
            {"type": event_type, "data": data or {}}
        ),
        _save_checkpoint=lambda: checkpoint_saves.append(True),
        _delete_checkpoint=lambda: deleted.append(True),
        _persist_memory_to_file=lambda: None,
    )
    runner = TaskRunner(orchestrator)
    runner._prepare = lambda _inputs: None

    def exhaust(*_args, **_kwargs):
        raise BudgetExhausted("model_calls", 1, 1)

    runner._execute = exhaust

    answer = runner.run("objetivo", None)

    assert answer
    assert state.terminal_disposition == "block"
    assert state.last_result["error_code"] == "TASK_BUDGET_EXHAUSTED"
    assert checkpoint_saves == [True]
    assert deleted == []
    assert orchestrator._preserve_checkpoint is True


def test_missing_objective_discards_stale_terminal_evidence() -> None:
    state = AgentState()
    state.terminal_disposition = "complete"
    state.last_result = {"status": "succeeded", "ok": True}
    deleted = []

    def reset_task(objective: str) -> None:
        state.reset_task_progression(())
        state.objective = objective
        state.last_result = None
        state.tool_history = []

    orchestrator = SimpleNamespace(
        session=SimpleNamespace(messages=[], config={}),
        agent_state=state,
        cancellation_token=_CancellationToken(),
        _load_checkpoint=lambda: None,
        _reset_task_state=reset_task,
        _task_failed=False,
        _cancelled=False,
        workspace=SimpleNamespace(rollback=lambda: None),
        context_manager=SimpleNamespace(maybe_compress_context=lambda: None),
        _emit=lambda event_type, data=None: state.events.append(
            {"type": event_type, "data": data or {}}
        ),
        _delete_checkpoint=lambda: deleted.append(True),
        _persist_memory_to_file=lambda: None,
    )

    answer = TaskRunner(orchestrator).run(None, None)

    assert answer
    assert state.terminal_disposition == "block"
    assert state.last_result["error_code"] == "MISSING_REQUIRED_INPUT"
    assert deleted == [True]


def test_invalid_checkpoint_is_explicit_non_success_and_preserved() -> None:
    state = AgentState()
    deleted = []
    orchestrator = SimpleNamespace(
        session=SimpleNamespace(messages=[], config={}),
        agent_state=state,
        cancellation_token=_CancellationToken(),
        _load_checkpoint=lambda: (_ for _ in ()).throw(
            CheckpointLoadError(
                Path("checkpoint.json"),
                "versão incompatível",
                reason_code="CHECKPOINT_INCOMPATIBLE_SCHEMA",
            )
        ),
        _delete_checkpoint=lambda: deleted.append(True),
        _persist_memory_to_file=lambda: None,
        _task_failed=False,
        _cancelled=False,
        _preserve_checkpoint=False,
        workspace=SimpleNamespace(rollback=lambda: None),
        context_manager=SimpleNamespace(maybe_compress_context=lambda: None),
        _emit=lambda event_type, data=None: state.events.append(
            {"type": event_type, "data": data or {}}
        ),
    )

    answer = TaskRunner(orchestrator).run(None, None)

    assert answer
    assert state.terminal_disposition == "block"
    assert state.last_result["error_code"] == "CHECKPOINT_INCOMPATIBLE_SCHEMA"
    assert orchestrator._preserve_checkpoint is True
    assert deleted == []


def test_terminal_complete_checkpoint_without_success_evidence_is_blocked() -> None:
    checkpoint_state = AgentState()
    checkpoint_state.objective = "tarefa terminal"
    checkpoint_state.terminal_disposition = "complete"
    checkpoint = checkpoint_state.to_checkpoint_dict()
    restored = AgentState()
    executed = []
    orchestrator = SimpleNamespace(
        session=SimpleNamespace(messages=[], config={}),
        agent_state=restored,
        cancellation_token=_CancellationToken(),
        _load_checkpoint=lambda: checkpoint,
        _delete_checkpoint=lambda: None,
        _save_checkpoint=lambda: None,
        _persist_memory_to_file=lambda: None,
        _task_failed=False,
        _cancelled=False,
        _preserve_checkpoint=False,
        workspace=SimpleNamespace(rollback=lambda: None),
        context_manager=SimpleNamespace(maybe_compress_context=lambda: None),
        _emit=lambda event_type, data=None: restored.events.append(
            {"type": event_type, "data": data or {}}
        ),
    )
    runner = TaskRunner(orchestrator)
    runner._execute = lambda *_args, **_kwargs: executed.append(True)

    answer = runner.run(None, None)

    assert answer
    assert executed == []
    assert restored.terminal_disposition == "block"
    assert restored.last_result["error_code"] == "CHECKPOINT_TERMINAL_EVIDENCE_MISSING"
    assert orchestrator._preserve_checkpoint is True
