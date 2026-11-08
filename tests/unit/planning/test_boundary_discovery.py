from dataclasses import replace
from types import SimpleNamespace

import pytest

from llm_agent.agent.planning.plan_builder import PlanBuilder, PlanningDecisionKind
from llm_agent.agent.planning.planning_context import PlanningContextError
from llm_agent.agent.runtime.correlation import RunCorrelation
from llm_agent.agent.state import AgentState
from tests.unit.planning.test_tool_disclosure import _context, _owner

OBJECTIVE = "Read the requested fixture."
COMPLETE = {"action": "complete", "reason": "observations suffice"}


def _fixture(responses, tmp_path, *, context=None, selected=("tool_a",)):
    context = context or _context()
    owner = _owner(context, [{"tools": list(selected)}, *responses])
    owner.run_correlation = RunCorrelation.fresh()
    owner.agent_state = AgentState()
    owner.final_responder = None
    events = []
    owner._emit = lambda event, data: events.append((event, data))
    builder = PlanBuilder(owner, analysis_notes_file=tmp_path / "notes.md")
    builder._tool_guidance(OBJECTIVE, force_refresh=False)
    return builder, owner, events


def _contracts(owner):
    return [kwargs["request_contract"].value for _args, kwargs in owner.calls]


def test_completion_keeps_model_review_without_repeating_discovery(tmp_path):
    builder, owner, events = _fixture([COMPLETE], tmp_path)
    result = builder.continue_after_reasoning_boundary(OBJECTIVE)
    assert result.kind is PlanningDecisionKind.COMPLETE
    assert _contracts(owner) == ["tool_discovery", "reasoning_boundary_continuation"]
    assert events[-1][1]["discovery_requests"] == 0
    assert events[-1][1]["cache_hit"] is True


def test_observations_and_progress_are_fresh_on_each_review(tmp_path):
    builder, owner, _events = _fixture([COMPLETE, COMPLETE], tmp_path)
    summary = ["FIRST_OBSERVATION"]
    owner.final_responder = SimpleNamespace(_tool_results_summary=lambda: summary[0])
    builder.continue_after_reasoning_boundary(OBJECTIVE)
    summary[0] = "SECOND_OBSERVATION"
    builder.continue_after_reasoning_boundary(OBJECTIVE)
    assert "FIRST_OBSERVATION" in owner.calls[1][0][0]
    assert "SECOND_OBSERVATION" in owner.calls[2][0][0]
    assert "FIRST_OBSERVATION" not in owner.calls[2][0][0]


def test_execution_with_existing_selection_keeps_exact_view(tmp_path):
    decision = {"action": "execute", "plan": [{"tool": "tool_a", "args": {}}]}
    builder, owner, _events = _fixture([decision], tmp_path)
    result = builder.continue_after_reasoning_boundary(OBJECTIVE)
    assert result.kind is PlanningDecisionKind.EXECUTE
    assert result.planning_view.presented_names == frozenset({"tool_a"})
    assert len(owner.calls) == 2


def test_new_tool_forces_one_fresh_discovery_and_a_new_decision_before_execution(tmp_path):
    first = {"action": "execute", "plan": [{"tool": "tool_b", "args": {"value": "unreviewed"}}]}
    final = {"action": "execute", "plan": [{"tool": "tool_b", "args": {"value": "reviewed"}}]}
    builder, owner, _events = _fixture([first, {"tools": ["tool_b"]}, final], tmp_path)
    result = builder.continue_after_reasoning_boundary(OBJECTIVE)
    assert _contracts(owner) == [
        "tool_discovery", "reasoning_boundary_continuation", "tool_discovery", "reasoning_boundary_continuation",
    ]
    assert result.plan[0].args["value"] == "reviewed"
    assert result.planning_view.presented_names == frozenset({"tool_b"})
    assert owner.agent_state.tool_history == []


def test_nested_deferred_new_tool_also_refreshes(tmp_path):
    decision = {"action": "execute", "plan": [{
        "kind": "deferred_condition", "observation_ref": 1,
        "predicate": {"op": "equals", "value": "yes"},
        "on_true": {"tool": "tool_b", "args": {}}, "on_false": {"waive_effect": "write"},
    }]}
    builder, owner, _events = _fixture([decision, {"tools": ["tool_b"]}, COMPLETE], tmp_path)
    assert builder.continue_after_reasoning_boundary(OBJECTIVE).kind is PlanningDecisionKind.COMPLETE
    assert len(owner.calls) == 4


@pytest.mark.parametrize("change", ["run", "task", "objective", "capabilities", "authority", "context_authority", "context", "budget", "view"])
def test_scope_changes_use_normal_discovery(change, tmp_path):
    builder, owner, _events = _fixture([{"tools": ["tool_b"]}, COMPLETE], tmp_path)
    objective = OBJECTIVE
    if change == "run":
        owner.run_correlation = RunCorrelation.fresh()
    elif change == "task":
        owner.run_correlation = owner.run_correlation.child("child")
    elif change == "objective":
        objective = "Read something different."
    elif change == "capabilities":
        owner.allowed_capabilities = frozenset({"analyze"})
    elif change == "authority":
        owner.task_authority = "changed authority snapshot"
    elif change == "context":
        context = replace(owner.planning_context, snapshot_id="new-context")
        owner.planning_context = context
        owner.get_planning_view = lambda kind: context.present(kind)
    elif change == "context_authority":
        context = replace(owner.planning_context, authority_identity="new-authority")
        owner.planning_context = context
        owner.get_planning_view = lambda kind: context.present(kind)
    elif change == "budget":
        owner.session.hardware_profile.context_limit = 4_096
    else:
        context = owner.planning_context
        tools = (*context.tools[:-1], replace(context.tools[-1], name="tool_f"))
        context = replace(context, tools=tools, eligible_names=frozenset(t.name for t in tools))
        owner.planning_context = context
        owner.get_planning_view = lambda kind: context.present(kind)
    assert builder.continue_after_reasoning_boundary(objective).kind is PlanningDecisionKind.COMPLETE
    assert _contracts(owner)[1:] == ["tool_discovery", "reasoning_boundary_continuation"]


def test_invalid_current_binding_fails_closed_before_review(tmp_path):
    builder, owner, _events = _fixture([COMPLETE], tmp_path)
    foreign = replace(owner.planning_context, snapshot_id="foreign").present("linear")
    owner.get_planning_view = lambda kind: foreign
    with pytest.raises(PlanningContextError):
        builder.continue_after_reasoning_boundary(OBJECTIVE)
    assert len(owner.calls) == 1


def test_invalid_review_does_not_create_an_extra_retry_loop(tmp_path):
    builder, owner, _events = _fixture([{"action": "invalid"}], tmp_path)
    assert builder.continue_after_reasoning_boundary(OBJECTIVE).kind is PlanningDecisionKind.FAIL
    assert len(owner.calls) == 2


def test_complete_review_preserves_obligations_for_canonical_completion_gate(tmp_path):
    obligation = {"id": "review:x", "kind": "read", "target": "x.txt", "description": "Read x.txt"}
    builder, _owner_object, _events = _fixture([{**COMPLETE, "obligations": [obligation]}], tmp_path)
    result = builder.continue_after_reasoning_boundary(OBJECTIVE)
    assert result.review_obligations[0]["target"] == "x.txt"


def test_reused_visibility_does_not_authorize_a_write(tmp_path):
    from llm_agent.agent.planning.plan_validator import PlanValidator

    context = _context(include_writer=True)
    decision = {"action": "execute", "plan": [{"tool": "writer", "args": {}}]}
    builder, owner, _events = _fixture([decision], tmp_path, context=context, selected=("writer",))
    result = builder.continue_after_reasoning_boundary(OBJECTIVE)
    validator = PlanValidator(
        {}, list(context.eligible_names), frozenset({"read"}), None,
        planning_context=context, planning_view=result.planning_view,
    )
    assert not validator.validate(result.plan).is_valid
    assert owner.agent_state.tool_history == []


def test_visibility_refresh_is_bounded_even_if_second_decision_requests_another_tool(tmp_path):
    from llm_agent.agent.planning.plan_validator import PlanValidator

    first = {"action": "execute", "plan": [{"tool": "tool_b", "args": {}}]}
    second = {"action": "execute", "plan": [{"tool": "tool_c", "args": {}}]}
    builder, owner, _events = _fixture([first, {"tools": ["tool_b"]}, second], tmp_path)
    result = builder.continue_after_reasoning_boundary(OBJECTIVE)
    assert len(owner.calls) == 4
    context = owner.planning_context
    validator = PlanValidator(
        {}, list(context.eligible_names), frozenset({"read"}), None,
        planning_context=context, planning_view=result.planning_view,
    )
    assert not validator.validate(result.plan).is_valid


def test_extra_visibility_review_still_obeys_actual_model_call_budget(tmp_path):
    from llm_agent.agent.runtime.budget import BudgetExhausted
    from tests.unit.planning.test_tool_disclosure import _real_seam_owner

    owner, gateway, session, _metrics, _events, _emitted = _real_seam_owner([
        '{"tools":["tool_a"]}',
        '{"action":"execute","plan":[{"tool":"tool_b","args":{}}]}',
        '{"tools":["tool_b"]}',
        '{"action":"complete","reason":"done"}',
    ])
    owner.run_correlation = RunCorrelation.fresh()
    owner.allowed_capabilities = frozenset({"read"})
    owner.agent_state = owner.context_manager.agent_state
    owner.final_responder = None
    session.budget_ledger.max_model_calls = 3
    builder = PlanBuilder(owner, analysis_notes_file=tmp_path / "notes.md")
    builder._tool_guidance(OBJECTIVE, force_refresh=False)
    with pytest.raises(BudgetExhausted):
        builder.continue_after_reasoning_boundary(OBJECTIVE)
    assert len(gateway.requests) == session.budget_ledger.snapshot().model_calls == 3
