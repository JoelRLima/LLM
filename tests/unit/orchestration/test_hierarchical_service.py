from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.orchestration import hierarchical_service as hierarchical_service_module
from agent.orchestration.hierarchical_service import HierarchicalExecutionService
from agent.orchestration.route_result import RouteDisposition, RouteResult
from agent.planning.hierarchical_planner import MacroPlan, MacroStep
from agent.runtime.budget import BudgetExhausted
from agent.runtime.paths import WorkspacePaths


class _Planner:
    result = None
    error = None

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    def build_plan(self, objective):
        del objective
        if self.error is not None:
            raise self.error
        return self.result


def _orchestrator(tmp_path: Path):
    workspace_paths = WorkspacePaths(
        workspace_id="hierarchical-tests",
        data_dir=tmp_path / "data",
        state_dir=tmp_path / "state",
        cache_dir=tmp_path / "cache",
    )
    workspace_paths.ensure_directories()
    events = []
    state = SimpleNamespace()
    orchestrator = SimpleNamespace(
        events=events,
        agent_state=state,
        context_manager=SimpleNamespace(model_name="test-model"),
        execution_gateway=None,
        final_responder=object(),
        plan_builder=object(),
        plan_executor=object(),
        session=SimpleNamespace(config={}),
        skills=[],
        workspace_paths=workspace_paths,
        _emit=lambda event_type, data=None: events.append(
            {"type": event_type, "data": data or {}}
        ),
        _summarize_text=lambda text: text,
    )
    orchestrator.get_planning_view = lambda _kind: SimpleNamespace(presented_names=[])
    return orchestrator


def _install_planner(monkeypatch, *, result=None, error=None) -> None:
    _Planner.result = result
    _Planner.error = error
    monkeypatch.setattr(hierarchical_service_module, "HierarchicalPlanner", _Planner)


def test_selector_control_flow_distinguishes_not_applicable_from_fallback() -> None:
    not_selected = RouteResult.not_applicable(
        "hierarchical", reason_code="HIERARCHICAL_NOT_SELECTED"
    )
    planner_fallback = RouteResult.fallback(
        "hierarchical",
        reason_code="HIERARCHICAL_PLANNER_ERROR",
        detail="planner error",
    )

    assert not_selected.disposition is RouteDisposition.NOT_APPLICABLE
    assert planner_fallback.disposition is RouteDisposition.FALLBACK
    assert not_selected.disposition is not planner_fallback.disposition


def test_hierarchical_service_requires_explicit_tracker_authority(tmp_path: Path) -> None:
    orchestrator = _orchestrator(tmp_path)
    orchestrator.workspace_paths = None

    with pytest.raises(RuntimeError, match="explicit workspace path authority"):
        HierarchicalExecutionService(orchestrator).run("objetivo")


def test_planner_exception_returns_explicit_safe_fallback(monkeypatch, tmp_path: Path) -> None:
    _install_planner(monkeypatch, error=RuntimeError("planner\nfailed"))
    orchestrator = _orchestrator(tmp_path)

    result = HierarchicalExecutionService(orchestrator).run("objetivo")

    assert result == RouteResult.fallback(
        "hierarchical",
        reason_code="HIERARCHICAL_PLANNER_ERROR",
        detail="RuntimeError: planner failed",
    )
    fallback_event = orchestrator.events[-1]
    assert fallback_event["type"] == "hierarchical_fallback"
    assert fallback_event["data"]["reason_code"] == "HIERARCHICAL_PLANNER_ERROR"
    assert "\n" not in result.detail


def test_planner_construction_exception_returns_explicit_fallback(
    monkeypatch, tmp_path: Path
) -> None:
    class _BrokenPlanner:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            raise RuntimeError("constructor\nfailed")

    monkeypatch.setattr(hierarchical_service_module, "HierarchicalPlanner", _BrokenPlanner)
    orchestrator = _orchestrator(tmp_path)

    result = HierarchicalExecutionService(orchestrator).run("objetivo")

    assert result.disposition is RouteDisposition.FALLBACK
    assert result.reason_code == "HIERARCHICAL_PLANNER_ERROR"
    assert result.detail == "RuntimeError: constructor failed"


def test_planning_context_failure_gets_non_recoverable_reason(
    monkeypatch, tmp_path: Path
) -> None:
    _install_planner(monkeypatch, result=MacroPlan(objective="objetivo", steps=[]))
    orchestrator = _orchestrator(tmp_path)
    orchestrator.get_planning_view = lambda _kind: (_ for _ in ()).throw(
        RuntimeError("planning context unavailable")
    )

    result = HierarchicalExecutionService(orchestrator).run("objetivo")

    assert result.disposition is RouteDisposition.FALLBACK
    assert result.reason_code == "HIERARCHICAL_PRECONDITION_UNAVAILABLE"


@pytest.mark.parametrize(
    "macro_plan",
    [None, MacroPlan(objective="objetivo", steps=[])],
    ids=["missing", "empty"],
)
def test_empty_or_non_useful_macroplan_returns_deterministic_fallback(
    monkeypatch, macro_plan, tmp_path: Path
) -> None:
    _install_planner(monkeypatch, result=macro_plan)
    orchestrator = _orchestrator(tmp_path)

    result = HierarchicalExecutionService(orchestrator).run("objetivo")

    assert result.disposition is RouteDisposition.FALLBACK
    assert result.route == "hierarchical"
    assert result.reason_code == "HIERARCHICAL_MACROPLAN_EMPTY"
    assert result.detail == "Hierarchical planner returned no usable macroplan."
    assert orchestrator.events[-1]["data"]["reason_code"] == result.reason_code


def test_budget_exhaustion_is_reraised_without_fallback(
    monkeypatch, tmp_path: Path
) -> None:
    error = BudgetExhausted("model_calls", 1, 1)
    _install_planner(monkeypatch, error=error)
    orchestrator = _orchestrator(tmp_path)

    with pytest.raises(BudgetExhausted) as raised:
        HierarchicalExecutionService(orchestrator).run("objetivo")

    assert raised.value is error
    assert orchestrator.events == []


def test_execution_returns_handled_answer_without_task_status_mutation(
    monkeypatch, tmp_path: Path
) -> None:
    plan = MacroPlan(
        objective="objetivo",
        steps=[MacroStep(id="step-1", title="Step", goal="Do the step")],
    )
    _install_planner(monkeypatch, result=plan)

    class _Tracker:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def start(self, objective, steps, metadata) -> None:
            self.started = (objective, steps, metadata)

    class _Executor:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def execute(self, macro_plan, state, context, *, on_chunk=None):
            self.execution = (macro_plan, state, context, on_chunk)
            return "canonical executor answer"

    monkeypatch.setattr(hierarchical_service_module, "TaskTracker", _Tracker)
    monkeypatch.setattr(hierarchical_service_module, "HierarchicalExecutor", _Executor)
    orchestrator = _orchestrator(tmp_path)

    result = HierarchicalExecutionService(orchestrator).run("objetivo")

    assert result.disposition is RouteDisposition.HANDLED
    assert result.route == "hierarchical"
    assert result.answer == "canonical executor answer"
    assert result.reason_code is None
    assert not hasattr(orchestrator.agent_state, "terminal_disposition")
    assert [event["type"] for event in orchestrator.events] == [
        "hierarchical_started",
        "hierarchical_completed",
    ]


def test_hard_execution_exception_returns_handled_non_success(
    monkeypatch, tmp_path: Path
) -> None:
    plan = MacroPlan(
        objective="objetivo",
        steps=[MacroStep(id="step-1", title="Step", goal="Do the step")],
    )
    _install_planner(monkeypatch, result=plan)

    class _Tracker:
        def __init__(self, **kwargs) -> None:
            del kwargs

        def start(self, objective, steps, metadata) -> None:
            del objective, steps, metadata

    class _Executor:
        def __init__(self, **kwargs) -> None:
            del kwargs

        def execute(self, *args, **kwargs):
            del args, kwargs
            raise RuntimeError("executor failed")

    monkeypatch.setattr(hierarchical_service_module, "TaskTracker", _Tracker)
    monkeypatch.setattr(hierarchical_service_module, "HierarchicalExecutor", _Executor)
    orchestrator = _orchestrator(tmp_path)
    orchestrator._task_failed = False

    result = HierarchicalExecutionService(orchestrator).run("objetivo")

    assert result.disposition is RouteDisposition.HANDLED
    assert result.reason_code == "HIERARCHICAL_EXECUTION_FAILED"
    assert result.answer == "A execucao hierarquica falhou."
    assert orchestrator._task_failed is True
    assert [event["type"] for event in orchestrator.events] == ["hierarchical_started"]
