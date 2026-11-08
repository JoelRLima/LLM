from dataclasses import FrozenInstanceError, replace

import pytest

from llm_agent.agent.planning.planning_context import PlanningTool
from llm_agent.agent.planning.presentation import PlanningPresentationError
from llm_agent.agent.planning.tool_disclosure import ToolDisclosureResult, disclose_tools, render_tool_guidance
from tests.unit.planning.test_tool_disclosure import _context, _owner


def test_guidance_and_metrics_reuse_the_exact_validated_strings(monkeypatch):
    context = _context()
    owner = _owner(context, [{"tools": ["tool_a", "tool_b"]}])
    result = disclose_tools(owner, planner_kind="linear", objective="Read")
    assert result is not None
    expected_index = result.index_view.render_index()
    expected_details = result.selected_view.render_detailed()

    def unexpected(*args, **kwargs):
        pytest.fail("unchanged validated snapshot was rendered again")

    monkeypatch.setattr(type(result.index_view), "render_index", unexpected)
    monkeypatch.setattr(type(result.selected_view), "render_detailed", unexpected)
    assert result.render_index(context_limit=8_192) == expected_index
    assert result.render_details(context_limit=8_192) == expected_details
    assert expected_index in render_tool_guidance(owner, result)
    assert expected_details in render_tool_guidance(owner, result)
    with pytest.raises(FrozenInstanceError):
        result._rendered_context_limit = 1
    assert "_rendered_details" not in repr(result)


def _large_context():
    context = _context(names=[f"tool_{number}" for number in range(4)])
    tools = tuple(PlanningTool(
        name=tool.name, description="D" * 1_000,
        input_schema={"type": "object", "properties": {"value": {"type": "string", "description": "S" * 1_500}}},
    ) for tool in context.tools)
    return replace(context, tools=tools)


def test_smaller_budget_revalidates_and_rejects_a_previously_valid_catalog():
    context = _large_context()
    owner = _owner(context, [])
    result = disclose_tools(owner, planner_kind="linear", objective="Read")
    assert result is not None
    assert len(result.render_details(context_limit=8_192)) > 8_192
    owner.session.hardware_profile.context_limit = 1
    with pytest.raises(PlanningPresentationError, match="budget"):
        render_tool_guidance(owner, result)


def test_initial_overflow_still_fails_before_any_guidance_or_metric():
    owner = _owner(_large_context(), [])
    owner.session.hardware_profile.context_limit = 1
    events = []
    owner._emit = lambda *args: events.append(args)
    with pytest.raises(PlanningPresentationError):
        disclose_tools(owner, planner_kind="linear", objective="Read")
    assert events == []


def test_replacing_the_selection_cannot_carry_old_cached_cards():
    context = _context()
    owner = _owner(context, [{"tools": ["tool_a"]}])
    original = disclose_tools(owner, planner_kind="linear", objective="Read")
    assert original is not None
    replacement = replace(
        original, selected_view=context.present("linear", {"tool_b"}), selected_names=frozenset({"tool_b"}),
    )
    text = replacement.render_details(context_limit=8_192)
    assert '"name":"tool_b"' in text
    assert '"name":"tool_a"' not in text


def test_directly_constructed_result_uses_normal_validation():
    context = _large_context()
    view = context.present("linear")
    result = ToolDisclosureResult(view, view, view.presented_names)
    assert result.render_details(context_limit=8_192) == view.render_detailed()
    with pytest.raises(PlanningPresentationError):
        result.render_details(context_limit=1)
    with pytest.raises(TypeError):
        ToolDisclosureResult(view, view, view.presented_names, _rendered_details="forged")


@pytest.mark.parametrize(("tool_count", "index_renders"), [(4, 100), (8, 100)])
def test_rendering_counts_drop_without_changing_baseline_prompt_or_events(tool_count, index_renders):
    from scripts.benchmark_tool_guidance import benchmark

    baseline = benchmark(100, tool_count, reuse_enabled=False)
    result = benchmark(100, tool_count)
    assert result["guidance_sha256"] == baseline["guidance_sha256"]
    assert result["events_sha256"] == baseline["events_sha256"]
    assert result["calls"]["detail_renders"] == result["calls"]["index_renders"] == index_renders
    assert result["calls"].get("selector_requests_simulated", 0) == baseline["calls"].get("selector_requests_simulated", 0)
