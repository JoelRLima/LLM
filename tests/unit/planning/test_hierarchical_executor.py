from types import SimpleNamespace

import pytest

from agent.execution_state import StepExecutionRecord, StepStatus
from agent.planning.hierarchical_executor import HierarchicalExecutor
from agent.planning.hierarchical_planner import HierarchicalPlanner, MacroPlan, MacroStep
from agent.planning.plan_builder import PlanBuildResult
from agent.reporting.operational_outcome import project_operational_outcome
from agent.runtime.budget import BudgetExhausted
from agent.runtime.outcome_taxonomy import OperationalStatus
from agent.state import AgentState
from agent.tools.result_completeness import (
    canonical_result_successful,
    legacy_result_successful,
)


class _State:
    def __init__(self):
        self.plan = []
        self.tool_history = []

    def set_plan(self, plan):
        self.plan = plan

    def clear_plan(self):
        self.plan = []


class _Gateway:
    def __init__(self, state):
        self.state = state
        self.calls = []

    def execute_validated_plan(self, plan, objective, tool_usage_count):
        self.calls.append((plan, objective, tool_usage_count))
        self.state.tool_history.append(
            {"tool": "echo", "args": {}, "result": {"ok": True, "message": "feito"}}
        )
        return SimpleNamespace(aborted=False, final_answer=None, validated_plan=plan)


class _Tracker:
    def mark_running(self, step_id):
        return None

    def record_tool_call(self, count):
        self.tool_calls = count

    def mark_completed(self, *args, **kwargs):
        self.completed = True

    def mark_failed(self, *args, **kwargs):
        raise AssertionError("o macro passo não deveria falhar")

    def finish_success(self, summary):
        self.finished = True

    def finish_failure(self, reason):
        raise AssertionError("o macro plano não deveria falhar")


class _Summarizer:
    def __init__(self):
        self.items = []

    def add(self, text):
        self.items.append(text)

    def force_flush(self):
        return None

    def get_accumulated_content(self):
        return "\n".join(self.items)


def test_hierarchical_flow_executes_each_microplan_through_gateway():
    state = _State()
    gateway = _Gateway(state)
    tracker = _Tracker()
    executor = HierarchicalExecutor(
        plan_builder=SimpleNamespace(
            build_plan=lambda goal: PlanBuildResult(
                plan=[{"tool": "echo", "args": {"text": goal}}]
            )
        ),
        plan_executor=object(),
        final_responder=SimpleNamespace(
            build_final_answer=lambda prompt, on_chunk=None, **_kwargs: "consolidado"
        ),
        context_manager=object(),
        session=SimpleNamespace(messages=[]),
        tracker=tracker,
        summarizer=_Summarizer(),
        execution_gateway=gateway,
    )
    macro_plan = MacroPlan(
        objective="objetivo amplo",
        steps=[MacroStep(id="s1", title="Etapa", goal="subobjetivo")],
    )

    answer = executor.execute(macro_plan, state, {})

    assert answer == "consolidado"
    assert len(gateway.calls) == 1
    assert gateway.calls[0][1] == "subobjetivo"
    assert tracker.completed is True
    assert state.plan == []


def test_hierarchical_microstep_failure_is_not_overwritten_by_later_success():
    state = SimpleNamespace(
        step_records={
            "failed": StepExecutionRecord("failed", status=StepStatus.FAILED),
            "later": StepExecutionRecord("later", status=StepStatus.COMPLETED),
        }
    )

    assert HierarchicalExecutor._has_failed_execution_record(state) is True


def test_hierarchical_step_success_uses_canonical_status_over_raw_ok() -> None:
    assert HierarchicalExecutor._determine_step_success(
        [{"result": {"ok": True, "status": "failed"}}]
    ) is False
    assert canonical_result_successful({"ok": True}) is False
    for legacy_status in ("complete", "completed", "success"):
        assert canonical_result_successful(
            {"ok": True, "status": legacy_status}
        ) is False
        assert legacy_result_successful(
            {"ok": True, "status": legacy_status}
        ) is True
    assert canonical_result_successful(
        {"ok": True, "status": OperationalStatus.SUCCEEDED}
    ) is True
    assert HierarchicalExecutor._determine_step_success(
        [{"result": {"ok": True}}]
    ) is True


def test_hierarchical_planner_does_not_swallow_budget_exhaustion() -> None:
    planner = HierarchicalPlanner(
        ask_model=lambda *_args: (_ for _ in ()).throw(
            BudgetExhausted("model_calls", 1, 1)
        ),
        valid_tools=[],
    )

    with pytest.raises(BudgetExhausted):
        planner.build_plan("objetivo")


def test_hierarchical_reasoning_boundary_rejects_before_gateway_prefix() -> None:
    state = _State()
    gateway_calls = []
    tracker = SimpleNamespace(
        mark_running=lambda _step_id: None,
        mark_failed=lambda step_id, **kwargs: setattr(tracker, "failed", (step_id, kwargs)),
        mark_completed=lambda *_args, **_kwargs: None,
        record_tool_call=lambda _count: None,
        failed=None,
    )

    class _GatewayWithoutBoundaryFlag:
        def execute_validated_plan(self, plan, objective, tool_usage_count):
            gateway_calls.append((plan, objective, tool_usage_count))

    executor = HierarchicalExecutor(
        plan_builder=SimpleNamespace(
            build_plan=lambda _goal: PlanBuildResult(
                plan=[{"tool": "echo", "args": {}}],
                continue_after_plan=True,
            )
        ),
        plan_executor=SimpleNamespace(orchestrator=SimpleNamespace()),
        final_responder=SimpleNamespace(),
        context_manager=object(),
        session=SimpleNamespace(messages=[]),
        tracker=tracker,
        summarizer=_Summarizer(),
        execution_gateway=_GatewayWithoutBoundaryFlag(),
    )

    ok = executor._execute_step(
        MacroStep(id="s1", title="Etapa", goal="subobjetivo"),
        state,
        {},
    )

    assert ok is False
    assert gateway_calls == []
    assert tracker.failed[0] == "s1"
    assert state.tool_history == []


class _LifecycleTracker:
    def __init__(self) -> None:
        self.failed_steps = []
        self.completed_steps = []
        self.finish_failure_reason = None
        self.finish_success_summary = None

    def mark_running(self, step_id):
        del step_id

    def record_tool_call(self, count):
        del count

    def mark_failed(self, step_id, **kwargs):
        self.failed_steps.append((step_id, kwargs))

    def mark_completed(self, step_id, **kwargs):
        self.completed_steps.append((step_id, kwargs))

    def finish_failure(self, reason):
        self.finish_failure_reason = reason

    def finish_success(self, summary):
        self.finish_success_summary = summary


class _HierarchicalGateway:
    def __init__(self, state):
        self.state = state
        self.calls = []

    def execute_validated_plan(self, plan, objective, tool_usage_count):
        self.calls.append((plan, objective, tool_usage_count))
        self.state.tool_history.append(
            {
                "tool": "file_reader",
                "args": {"file_path": "controle.txt"},
                "result": {
                    "ok": True,
                    "status": "succeeded",
                    "executed": True,
                    "data": "observado",
                    "complete": True,
                },
            }
        )
        return SimpleNamespace(aborted=False, final_answer=None, validated_plan=plan)


def _hierarchical_executor_for_state(state, tracker, decisions):
    orchestrator = SimpleNamespace(
        agent_state=state,
        _task_failed=False,
        _cancelled=False,
        fail_task=lambda: setattr(orchestrator, "_task_failed", True),
        tool_registry=None,
        _emit=lambda *_args, **_kwargs: None,
    )
    plan_executor = SimpleNamespace(orchestrator=orchestrator, last_projection=None)
    gateway = _HierarchicalGateway(state)
    executor = HierarchicalExecutor(
        plan_builder=SimpleNamespace(build_plan=lambda _goal: next(decisions)),
        plan_executor=plan_executor,
        final_responder=SimpleNamespace(
            build_final_answer=lambda *_args, **_kwargs: "modelo nao deveria ser consultado"
        ),
        context_manager=object(),
        session=SimpleNamespace(messages=[]),
        tracker=tracker,
        summarizer=_Summarizer(),
        execution_gateway=gateway,
    )
    return executor, orchestrator, gateway


def test_hierarchical_child_failure_cannot_be_overwritten_by_later_child_success() -> None:
    state = AgentState()
    tracker = _LifecycleTracker()
    decisions = iter(
        [
            PlanBuildResult(blocked_answer="primeiro filho falhou"),
            PlanBuildResult(plan=[{"tool": "file_reader", "args": {"file_path": "controle.txt"}}]),
        ]
    )
    executor, orchestrator, _gateway = _hierarchical_executor_for_state(
        state, tracker, decisions
    )

    answer = executor.execute(
        MacroPlan(
            objective="objetivo amplo",
            steps=[
                MacroStep(id="failed", title="Falha", goal="falhar"),
                MacroStep(id="later", title="Depois", goal="continuar"),
            ],
        ),
        state,
        {},
    )

    assert "tarefa" in answer.casefold()
    assert tracker.finish_failure_reason
    assert tracker.finish_success_summary is None
    assert orchestrator._task_failed is True
    assert project_operational_outcome(state, task_failed=True).terminal_status == "failed"


def test_hierarchical_child_success_cannot_bypass_parent_pending_obligation() -> None:
    state = AgentState()
    state.initialize_task_semantics("escreva o arquivo controle.txt")
    tracker = _LifecycleTracker()
    decisions = iter(
        [PlanBuildResult(plan=[{"tool": "file_reader", "args": {"file_path": "controle.txt"}}])]
    )
    executor, _orchestrator, _gateway = _hierarchical_executor_for_state(
        state, tracker, decisions
    )

    answer = executor.execute(
        MacroPlan(
            objective="escreva o arquivo controle.txt",
            steps=[MacroStep(id="read", title="Leitura", goal="ler")],
        ),
        state,
        {},
    )

    assert "pendente" in answer.casefold()
    assert tracker.finish_failure_reason
    assert tracker.finish_success_summary is None
    assert state.terminal_disposition == "block"
