from dataclasses import replace

import pytest

from llm_agent.agent.planning.disclosure_cache import MAX_CACHED_SELECTIONS
from llm_agent.agent.planning.presentation import PlanningPresentationError
from llm_agent.agent.planning.tool_disclosure import disclose_tools
from llm_agent.agent.runtime.correlation import RunCorrelation
from tests.unit.planning.test_tool_disclosure import _context, _owner


def _fixture(responses):
    owner = _owner(_context(), responses)
    owner.run_correlation = RunCorrelation.fresh()
    return owner


def _select(owner, objective="inspect", **kwargs):
    return disclose_tools(owner, planner_kind="linear", objective=objective, **kwargs)


def _change_planning_view(owner, context, change):
    if change == "context_authority":
        context = replace(context, authority_identity="changed")
    elif change == "description":
        context = replace(context, tools=(replace(context.tools[0], description="Changed purpose"), *context.tools[1:]))
    elif change == "schema":
        context = replace(context, tools=(replace(context.tools[0], input_schema={"type": "object", "required": ["path"]}, usage_examples=()), *context.tools[1:]))
    else:
        tools = (*context.tools, replace(context.tools[-1], name="tool_f"))
        context = replace(context, tools=tools, eligible_names=frozenset(t.name for t in tools))
    owner.planning_context = context
    owner.get_planning_view = lambda kind: context.present(kind)


def test_same_task_and_exact_view_reuse_the_selection_without_a_model_call():
    owner = _fixture([{"tools": ["tool_a"]}])
    original = _select(owner)
    reused = _select(owner)
    assert original.selected_names == reused.selected_names == frozenset({"tool_a"})
    assert reused.cache_hit and reused.discovery_requests == 0
    assert len(owner.calls) == 1


@pytest.mark.parametrize("change", [
    "run", "task", "authority", "application_authority", "capabilities", "context_authority",
    "description", "schema", "view", "budget", "objective", "missing_correlation",
])
def test_scope_changes_rediscover_even_when_snapshot_id_and_names_overlap(change):
    owner = _fixture([{"tools": ["tool_a"]}, {"tools": ["tool_b"]}])
    _select(owner)
    context = owner.planning_context
    objective = "inspect"
    if change == "run":
        owner.run_correlation = RunCorrelation.fresh()
    elif change == "task":
        owner.run_correlation = owner.run_correlation.child("child")
    elif change == "authority":
        owner.task_authority = "new-task-authority"
    elif change == "application_authority":
        owner.application_authority = "new-application-authority"
    elif change == "capabilities":
        owner.allowed_capabilities = frozenset({"read", "analyze"})
    elif change == "budget":
        owner.session.hardware_profile.context_limit = 4096
    elif change == "objective":
        objective = "inspect something else"
    elif change == "missing_correlation":
        del owner.run_correlation
    else:
        _change_planning_view(owner, context, change)
    result = _select(owner, objective)
    assert result.selected_names == frozenset({"tool_b"})
    assert not result.cache_hit and result.discovery_requests == 1
    assert len(owner.calls) == 2


def test_cache_has_a_fixed_bound_and_evicts_least_recently_used_selection():
    owner = _fixture([{"tools": ["tool_a"]}] * (MAX_CACHED_SELECTIONS + 2))
    for number in range(MAX_CACHED_SELECTIONS):
        _select(owner, str(number))
    assert _select(owner, "0").cache_hit
    _select(owner, "extra")
    assert len(owner._tool_disclosure_cache) == MAX_CACHED_SELECTIONS
    assert _select(owner, "0").cache_hit
    assert not _select(owner, "1").cache_hit
    assert len(owner.calls) == MAX_CACHED_SELECTIONS + 2


def test_invalid_forced_refresh_cannot_resurrect_an_old_valid_selection():
    owner = _fixture([{"tools": ["tool_a"]}, {"tools": ["hidden"]}, {"tools": ["hidden"]}, {"tools": ["tool_b"]}])
    _select(owner)
    failed = _select(owner, force_refresh=True)
    assert not failed.selection_valid
    result = _select(owner)
    assert not result.cache_hit
    assert result.selected_names == frozenset({"tool_b"})
    assert len(owner.calls) == 4


def test_presentation_failure_never_publishes_a_selection_to_the_cache(monkeypatch):
    from llm_agent.agent.planning import tool_disclosure

    owner = _fixture([{"tools": ["tool_a"]}, {"tools": ["tool_b"]}])
    original = tool_disclosure._result

    def overflow(*args, **kwargs):
        raise PlanningPresentationError("budget")

    monkeypatch.setattr(tool_disclosure, "_result", overflow)
    with pytest.raises(PlanningPresentationError):
        _select(owner)
    assert not owner._tool_disclosure_cache
    monkeypatch.setattr(tool_disclosure, "_result", original)
    assert _select(owner).selected_names == frozenset({"tool_b"})
    assert len(owner.calls) == 2


def test_legacy_name_only_cache_is_ignored():
    owner = _fixture([{"tools": ["tool_b"]}])
    owner._tool_disclosure_cache = {("context", "linear", "inspect"): frozenset({"tool_a"})}
    assert _select(owner).selected_names == frozenset({"tool_b"})
    assert len(owner.calls) == 1
