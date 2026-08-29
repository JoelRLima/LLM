"""Deterministic real-dispatch traces for representative TaskRunner routes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.orchestration import route_coordinator, task_runner
from agent.orchestration.operations import OrchestratorOperations
from agent.orchestration.route_result import RouteResult
from agent.orchestration.task_runner import TaskInputs, TaskRunner
from agent.planning.execution_gateway import ExecutionResult
from agent.planning.plan_builder import PlanBuildResult, PlanningDecisionKind
from agent.planning.plan_model import Plan
from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_dispatch import RuntimeEventDispatcher
from agent.state import AgentState

RUN_ID = "run-route-trace"
ROOT_ID = "task-route-root"


class _TraceOwner:
    _emit = OrchestratorOperations._emit

    def __init__(self, route: str) -> None:
        self.route = route
        self.agent_state = AgentState()
        self.run_correlation = RunCorrelation(RUN_ID, ROOT_ID, ROOT_ID)
        self.event_dispatcher = RuntimeEventDispatcher(state=self.agent_state)
        self.verbose = False
        self._task_failed = False
        self._cancelled = False
        self.session = SimpleNamespace(config={})
        self.tool_registry = None
        self.plan_builder = SimpleNamespace(build_plan=self._build_plan)
        self.execution_gateway = SimpleNamespace(execute_validated_plan=self._execute_plan)
        self.final_responder = SimpleNamespace(
            build_final_answer=lambda *_args, **_kwargs: "linear answer"
        )

    def _ensure_run_correlation(self) -> RunCorrelation:
        return self.run_correlation

    def _route_persona(self, _objective: str) -> None:
        return None

    def _save_checkpoint(self) -> bool:
        return True

    def _is_security_objective(self, _objective: str) -> bool:
        return self.route == "security"

    def _run_hierarchical(self, _objective: str, _on_chunk: object) -> RouteResult:
        self._emit("hierarchical_started", {"steps": 1})
        self._emit("hierarchical_completed", {"steps": 1})
        self.agent_state.terminal_disposition = "complete"
        return RouteResult.handled("hierarchical", answer="hierarchical answer")

    def _handle_security_analysis(self, _objective: str, _on_chunk: object) -> RouteResult:
        self._emit("plan_created", {"steps": 1, "mode": "security"})
        self.agent_state.terminal_disposition = "complete"
        return RouteResult.handled("security", answer="security answer")

    def _run_reactive(self, _objective: str, _usage: object, _count: int) -> str:
        self._emit("final", {"answer": "reactive answer"})
        self.agent_state.terminal_disposition = "complete"
        return "reactive answer"

    def _build_plan(self, _objective: str) -> PlanBuildResult:
        if self.route == "reactive":
            return PlanBuildResult(kind=PlanningDecisionKind.REPLAN)
        if self.route == "direct-answer":
            return PlanBuildResult(
                kind=PlanningDecisionKind.COMPLETE, direct_answer="direct answer"
            )
        plan = Plan.from_raw(
            [{"tool": "noop", "args": {}, "_step_id": "step-linear"}]
        )
        return PlanBuildResult(kind=PlanningDecisionKind.EXECUTE, plan=plan)

    def _execute_plan(self, plan: Plan, *_args: object, **_kwargs: object) -> ExecutionResult:
        self._emit("plan_created", {"steps": len(plan), "route": "linear"})
        self.agent_state.terminal_disposition = "complete"
        return ExecutionResult(final_answer="linear answer", validated_plan=plan)


@pytest.mark.parametrize(
    ("route", "expected_types"),
    [
        ("linear", ("plan_created",)),
        ("reactive", ("route_transition", "final")),
        ("hierarchical", ("hierarchical_started", "hierarchical_completed")),
        ("security", ("plan_created",)),
        ("direct-answer", ("task_outcome",)),
    ],
)
def test_representative_task_runner_routes_preserve_explicit_runtime_ids(
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    expected_types: tuple[str, ...],
) -> None:
    monkeypatch.setattr(task_runner, "allow_linear_completion", lambda *_args: None)
    monkeypatch.setattr(route_coordinator, "allow_linear_completion", lambda *_args: None)
    owner = _TraceOwner(route)
    runner = TaskRunner(owner)
    runner._route_is_hierarchical = lambda _objective: route == "hierarchical"

    answer = runner._execute(TaskInputs("representative objective", False, 0), None)

    assert answer
    events = owner.agent_state.events
    assert tuple(event["type"] for event in events) == expected_types
    for event in events:
        assert event["run_id"] == RUN_ID
        assert event["root_task_id"] == ROOT_ID
        assert event["task_id"] == ROOT_ID
        assert event["data"]["run_id"] == RUN_ID
        assert event["data"]["root_task_id"] == ROOT_ID
        assert event["data"]["task_id"] == ROOT_ID
    assert owner.run_correlation == RunCorrelation(RUN_ID, ROOT_ID, ROOT_ID)
