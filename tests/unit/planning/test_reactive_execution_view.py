from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from llm_agent.agent.llm.admitted_decisions import ReactiveToolDecision
from llm_agent.agent.planning.planning_context import (
    PlanningContextError,
    build_planning_context,
)
from llm_agent.agent.planning.planning_view_support import execution_planning_view
from llm_agent.agent.planning.presentation import validate_planning_view_binding
from llm_agent.agent.planning.reactive_loop import ReactiveLoop
from llm_agent.agent.tools.authority import ApplicationAuthoritySnapshot
from llm_agent.agent.tools.contracts import ToolDescriptor
from llm_agent.agent.tools.runtime_identity import RuntimeSnapshotIdentity


class _Registry:
    def __init__(self, descriptors, identity):
        self._descriptors = descriptors
        self.frozen = True
        self.runtime_identity = identity

    def descriptors(self):
        return self._descriptors


def _context():
    identity = RuntimeSnapshotIdentity.create("workspace")
    registry = _Registry(
        (
            ToolDescriptor("echo", "Echo text", schema={"type": "object"}),
            ToolDescriptor("grep", "Search text", schema={"type": "object"}),
        ),
        identity,
    )
    return build_planning_context(
        registry,
        ApplicationAuthoritySnapshot(runtime_identity=identity),
        None,
        None,
    )


def test_reactive_view_projects_to_linear_with_same_canonical_names():
    context = _context()
    source = context.present("reactive", {"echo"})

    projected = execution_planning_view(
        SimpleNamespace(planning_context=context),
        source,
    )

    assert projected is not None
    assert projected.planner_kind == "linear"
    assert projected.presented_names == source.presented_names == frozenset({"echo"})
    assert projected.tools == source.tools
    assert projected.planning_context_id == context.snapshot_id
    assert projected.runtime_identity == context.runtime_identity
    validate_planning_view_binding(context, projected, "linear")


def test_execution_view_rejects_wrong_kind_and_stale_context_identity():
    context = _context()
    orchestrator = SimpleNamespace(planning_context=context)

    with pytest.raises(PlanningContextError, match="planner kind diverge"):
        execution_planning_view(orchestrator, context.present("hierarchical", {"echo"}))

    stale = replace(
        context.present("reactive", {"echo"}),
        planning_context_id="stale-context",
    )
    with pytest.raises(PlanningContextError, match="context e view divergem"):
        execution_planning_view(orchestrator, stale)

    other_context = _context()
    with pytest.raises(PlanningContextError, match="context e view divergem"):
        execution_planning_view(
            SimpleNamespace(planning_context=other_context),
            context.present("reactive", {"echo"}),
        )


def test_reactive_gateway_receives_only_validated_linear_projection():
    context = _context()
    source = context.present("reactive", {"echo"})
    received = []

    class Gateway:
        def execute_validated_plan(self, plan, objective, usage, **kwargs):
            view = kwargs["planning_view"]
            validate_planning_view_binding(context, view, "linear")
            received.append((plan, objective, usage, view))
            return SimpleNamespace(aborted=False, final_answer=None)

    state = SimpleNamespace(plan_step=0, set_plan_step=lambda value: None)
    orchestrator = SimpleNamespace(
        planning_context=context,
        agent_state=state,
        execution_gateway=Gateway(),
        tool_registry=None,
    )
    loop = ReactiveLoop(orchestrator)
    loop._last_planning_view = source

    result = loop._handle_decision(
        ReactiveToolDecision(tool="echo", args={}),
        "echo this",
        {},
        1,
    )

    assert result is None
    assert len(received) == 1
    plan, objective, usage, view = received[0]
    assert plan == [{"tool": "echo", "args": {}}]
    assert objective == "echo this"
    assert usage == {}
    assert view.planner_kind == "linear"
    assert view.presented_names == source.presented_names


def test_execution_view_without_selector_view_remains_absent():
    assert execution_planning_view(SimpleNamespace(planning_context=_context()), None) is None
