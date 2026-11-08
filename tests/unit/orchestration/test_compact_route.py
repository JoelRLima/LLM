from __future__ import annotations

from types import SimpleNamespace

import pytest

from llm_agent.agent.orchestration import task_execution
from llm_agent.agent.orchestration.compact_route import use_compact_read_route
from llm_agent.agent.orchestration.route_result import RouteResult
from llm_agent.agent.planning.task_semantics import TaskSemantics
from llm_agent.agent.runtime.config_errors import ConfigError
from llm_agent.agent.runtime.config_repository import packaged_config_defaults
from llm_agent.agent.runtime.config_schema import validate_config_document
from llm_agent.agent.runtime.task_directives import DeliberationProfile, TaskDirective, TaskRunDirective


def _eligible(*, directive=TaskDirective.READ, profile=DeliberationProfile.NORMAL,
              capabilities=frozenset({"read"}), objective="Read notes.txt"):
    state = SimpleNamespace(
        task_run_directive=TaskRunDirective(directive, profile, objective),
        task_semantics=TaskSemantics.from_objective(objective),
    )
    owner = SimpleNamespace(
        session=SimpleNamespace(config={"agent_execution_profile": "compact"}),
        agent_state=state,
        allowed_capabilities=capabilities,
    )
    return owner, SimpleNamespace(resumed=False, objective=objective)


def test_compact_predicate_defaults_to_standard_and_admits_typed_small_read():
    owner, inputs = _eligible()
    owner.session.config = {}
    assert not use_compact_read_route(owner, inputs)
    owner.session.config["agent_execution_profile"] = "compact"
    assert use_compact_read_route(owner, inputs)


@pytest.mark.parametrize("change", [
    {"directive": TaskDirective.DO},
    {"directive": TaskDirective.PLAN},
    {"profile": DeliberationProfile.CAUTIOUS},
    {"capabilities": frozenset({"read", "write"})},
    {"capabilities": frozenset()},
    {"objective": "x" * 221},
])
def test_compact_predicate_rejects_out_of_bounds_or_full_deliberation(change):
    owner, inputs = _eligible(**change)
    assert not use_compact_read_route(owner, inputs)


def test_compact_predicate_rejects_resumed_and_mutating_semantics():
    owner, inputs = _eligible(objective="Update notes.txt")
    # Even if capability admission were accidentally permissive, effect intent
    # and checkpoint continuation independently disqualify the fast route.
    owner.allowed_capabilities = frozenset({"read"})
    assert not use_compact_read_route(owner, inputs)
    owner, inputs = _eligible()
    inputs.resumed = True
    assert not use_compact_read_route(owner, inputs)


def test_config_schema_accepts_compact_and_rejects_unknown_profile():
    config = packaged_config_defaults()
    config["agent_execution_profile"] = "compact"
    validate_config_document(config, require_version=True, require_complete=True)
    config["agent_execution_profile"] = "turbo"
    with pytest.raises(ConfigError):
        validate_config_document(config, require_version=True, require_complete=True)
    legacy = packaged_config_defaults()
    legacy.pop("agent_execution_profile", None)
    validate_config_document(legacy, require_version=True, require_complete=True)


def _execution_fixture(directive=TaskDirective.READ):
    objective = "Read notes.txt"
    transitions = []
    state = SimpleNamespace(
        task_run_directive=TaskRunDirective(directive, DeliberationProfile.NORMAL, objective),
        task_semantics=TaskSemantics.from_objective(objective),
        tool_history=[],
        terminal_disposition=None,
        project_last_result=lambda *_: None,
    )
    owner = SimpleNamespace(
        session=SimpleNamespace(config={"agent_execution_profile": "compact"}),
        agent_state=state,
        allowed_capabilities=frozenset({"read"}),
        _save_checkpoint=lambda: True,
        _run_reactive=lambda *_: setattr(state, "terminal_disposition", "complete") or "reactive",
    )
    runner = SimpleNamespace(
        orchestrator=owner,
        _try_hierarchical=lambda *_: RouteResult.fallback("hierarchical", reason_code="NO_MATCH"),
        _try_security=lambda *_: RouteResult.fallback("security", reason_code="NO_MATCH"),
        _consume_route_result=lambda result, _objective, **_kwargs: result.answer if result.handled else None,
        _emit_route_transition=lambda *args, **kwargs: transitions.append((args, kwargs)),
    )
    return runner, SimpleNamespace(objective=objective, resumed=False, original_message_count=0), transitions


def test_execute_task_compact_uses_reactive_after_hierarchy_and_security(monkeypatch):
    runner, inputs, transitions = _execution_fixture()
    calls = []
    runner._try_hierarchical = lambda *_: calls.append("hierarchy") or RouteResult.fallback("hierarchical", reason_code="NO_MATCH")
    runner._try_security = lambda *_: calls.append("security") or RouteResult.fallback("security", reason_code="NO_MATCH")
    runner.orchestrator.plan_builder = SimpleNamespace(build_plan=lambda *_: pytest.fail("linear planner called"))
    monkeypatch.setattr(task_execution, "_prepare_runtime_authority", lambda *_: None)
    assert task_execution.execute_task(runner, inputs, None) == "reactive"
    assert calls == ["hierarchy", "security"]
    assert transitions[0][1]["next_route"] == "reactive"


def test_execute_task_security_handled_wins_and_plan_preview_keeps_precedence(monkeypatch):
    runner, inputs, _ = _execution_fixture()
    monkeypatch.setattr(task_execution, "_prepare_runtime_authority", lambda *_: None)
    runner._try_security = lambda *_: RouteResult.handled("security", answer="security answer")
    runner.orchestrator.plan_builder = SimpleNamespace(build_plan=lambda *_: pytest.fail("linear planner called"))
    assert task_execution.execute_task(runner, inputs, None) == "security answer"

    preview_runner, preview_inputs, _ = _execution_fixture(TaskDirective.PLAN)
    monkeypatch.setattr(task_execution, "run_plan_preview", lambda _owner, subject: f"preview:{subject}")
    assert task_execution.execute_task(preview_runner, preview_inputs, None) == "preview:Read notes.txt"
