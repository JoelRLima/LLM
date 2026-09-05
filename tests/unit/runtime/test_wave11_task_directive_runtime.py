from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.llm.session import ChatSession
from agent.llm.session_requests import resolve_effective_reasoning_budget
from agent.orchestration import task_runner as task_runner_module
from agent.orchestration.operational_modes import refresh_capability_projection
from agent.orchestration.task_directive_runtime import (
    apply_task_run_directive_runtime,
    restore_task_run_directive_runtime,
)
from agent.orchestration.task_runner import TaskRunner
from agent.runtime.task_directives import (
    DeliberationProfile,
    TaskDirective,
    TaskRunDirective,
)
from agent.state import AgentState
from agent.tools.authority import OperationalMode, TaskAuthoritySnapshot

READ_CAPABILITIES = frozenset({"read", "vcs_read", "analyze"})
BROAD_CAPABILITIES = frozenset(
    {"read", "vcs_read", "analyze", "write", "validate", "process", "network", "memory"}
)


class _CancellationToken:
    def reset(self) -> None:
        pass

    def cancel(self) -> None:
        pass


def _owner(
    session: object | None = None,
    *,
    persona: frozenset[str] | None = BROAD_CAPABILITIES,
    mode: OperationalMode | None = OperationalMode.FULL,
    task_authority: TaskAuthoritySnapshot | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        session=session or SimpleNamespace(messages=[], thinking_budget=777),
        agent_state=AgentState(),
        cancellation_token=_CancellationToken(),
        _persona_allowed_capabilities=persona,
        _operational_mode=mode,
        _task_directive_capability_ceiling=None,
        task_authority=task_authority,
        tool_registry=None,
        allowed_capabilities=frozenset(),
    )


@pytest.mark.parametrize(
    ("mode", "task_authority", "expected"),
    [
        (OperationalMode.FULL, None, READ_CAPABILITIES),
        (OperationalMode.EDITOR, None, frozenset({"read", "vcs_read", "analyze"})),
        (OperationalMode.READ_ONLY, None, READ_CAPABILITIES),
        (
            OperationalMode.FULL,
            TaskAuthoritySnapshot(frozenset({"read", "write"})),
            frozenset({"read"}),
        ),
    ],
)
def test_read_is_a_restrictive_capability_intersection(
    mode: OperationalMode,
    task_authority: TaskAuthoritySnapshot | None,
    expected: frozenset[str],
) -> None:
    owner = _owner(mode=mode, task_authority=task_authority)
    refresh_capability_projection(owner)
    before = owner.allowed_capabilities

    restore = apply_task_run_directive_runtime(
        owner,
        TaskRunDirective(TaskDirective.READ, DeliberationProfile.NORMAL, "inspect"),
    )

    assert owner.allowed_capabilities <= before
    assert owner.allowed_capabilities <= READ_CAPABILITIES
    assert owner.allowed_capabilities == expected

    restore_task_run_directive_runtime(owner, restore)
    assert owner._task_directive_capability_ceiling is None
    assert owner.allowed_capabilities == before


def test_read_does_not_change_operational_mode_or_call_its_setter() -> None:
    owner = _owner(mode=OperationalMode.EDITOR)
    owner.set_operational_mode = lambda _mode: pytest.fail("READ must not set OperationalMode")
    refresh_capability_projection(owner)
    before_mode = owner._operational_mode

    restore = apply_task_run_directive_runtime(
        owner,
        TaskRunDirective(TaskDirective.READ, DeliberationProfile.SMART, "inspect"),
    )

    assert owner._operational_mode is before_mode is OperationalMode.EDITOR

    restore_task_run_directive_runtime(owner, restore)
    assert owner._operational_mode is OperationalMode.EDITOR


def test_do_cannot_widen_narrow_task_authority() -> None:
    owner = _owner(
        task_authority=TaskAuthoritySnapshot(frozenset({"read", "analyze"}))
    )
    refresh_capability_projection(owner)
    before = owner.allowed_capabilities

    restore = apply_task_run_directive_runtime(
        owner,
        TaskRunDirective(TaskDirective.DO, DeliberationProfile.SMART, "execute"),
    )

    assert owner.allowed_capabilities == before

    restore_task_run_directive_runtime(owner, restore)


@pytest.mark.parametrize(
    ("profile", "expected_budget"),
    [
        (DeliberationProfile.ECONOMY, 0),
        (DeliberationProfile.NORMAL, 777),
        (DeliberationProfile.SMART, 1024),
        (DeliberationProfile.CAUTIOUS, 2048),
    ],
)
def test_profile_changes_only_canonical_request_reasoning_budget(
    profile: DeliberationProfile,
    expected_budget: int,
) -> None:
    config = {
        "api_url": "http://127.0.0.1:8080",
        "model": "baseline-model",
        "temperature": 0.25,
        "max_tokens": 321,
    }
    gateway = object()
    session = ChatSession("system", config, gateway=gateway)
    session.thinking_budget = 777
    baseline_profile = session.model_profile
    baseline_config = dict(session.config)
    owner = _owner(session)

    restore = apply_task_run_directive_runtime(
        owner,
        TaskRunDirective(TaskDirective.DO, profile, "objective"),
    )
    request = session.build_request(stream=False)

    assert request.reasoning_budget == resolve_effective_reasoning_budget(
        expected_budget,
        request.max_output_tokens,
        baseline_profile.capabilities.reasoning,
    )
    assert request.model == baseline_profile.model
    assert request.temperature == baseline_profile.temperature
    assert request.max_output_tokens == baseline_profile.max_output_tokens
    assert session.model_profile is baseline_profile
    assert session.gateway is gateway
    assert session.config == baseline_config

    restore_task_run_directive_runtime(owner, restore)
    assert session.thinking_budget == 777


@pytest.mark.parametrize(
    ("profile", "expected_budget"),
    [
        (DeliberationProfile.ECONOMY, 0),
        (DeliberationProfile.NORMAL, 777),
        (DeliberationProfile.SMART, 1024),
        (DeliberationProfile.CAUTIOUS, 2048),
    ],
)
def test_task_runner_cleanup_restores_budget_and_projection(
    profile: DeliberationProfile,
    expected_budget: int,
) -> None:
    session = SimpleNamespace(thinking_budget=777)
    owner = _owner(session)
    refresh_capability_projection(owner)
    before = owner.allowed_capabilities
    runner = TaskRunner(owner)
    directive = TaskRunDirective(TaskDirective.DO, profile, "objective")

    runner._directive_runtime_restore = apply_task_run_directive_runtime(owner, directive)
    assert session.thinking_budget == expected_budget
    runner._cleanup = lambda _count: None
    runner._cleanup_after_run(0)

    assert session.thinking_budget == 777
    assert owner._task_directive_capability_ceiling is None
    assert owner.allowed_capabilities == before


def test_economy_disables_only_task_hierarchical_route(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        task_runner_module,
        "is_hierarchical",
        lambda objective: calls.append(objective) or True,
    )
    owner = _owner()
    owner.agent_state.task_run_directive = TaskRunDirective(
        TaskDirective.DO, DeliberationProfile.ECONOMY, "objective"
    )

    assert TaskRunner(owner)._route_is_hierarchical("objective") is False
    assert calls == []


@pytest.mark.parametrize("profile", [
    DeliberationProfile.NORMAL,
    DeliberationProfile.SMART,
    DeliberationProfile.CAUTIOUS,
])
def test_non_economy_profiles_preserve_baseline_hierarchical_route(
    monkeypatch,
    profile: DeliberationProfile,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        task_runner_module,
        "is_hierarchical",
        lambda objective: calls.append(objective) or True,
    )
    owner = _owner()
    owner.agent_state.task_run_directive = TaskRunDirective(
        TaskDirective.DO, profile, "objective"
    )

    assert TaskRunner(owner)._route_is_hierarchical("objective") is True
    assert calls == ["objective"]


def _shortcut_owner() -> SimpleNamespace:
    return SimpleNamespace(
        session=SimpleNamespace(messages=[], config={}),
        agent_state=AgentState(),
        cancellation_token=_CancellationToken(),
        _cancelled=False,
        _preserve_checkpoint=False,
        _task_failed=False,
        _answer_trivial=lambda objective: f"answer: {objective}",
    )


@pytest.mark.parametrize(
    ("directive", "expected_shortcut"),
    [
        (TaskRunDirective(TaskDirective.AUTO, DeliberationProfile.NORMAL, "oi"), True),
        (TaskRunDirective(TaskDirective.AUTO, DeliberationProfile.CAUTIOUS, "oi"), False),
        (TaskRunDirective(TaskDirective.PLAN, DeliberationProfile.ECONOMY, "oi"), False),
    ],
)
def test_cautious_and_plan_reach_task_definition_instead_of_trivial_shortcut(
    monkeypatch,
    directive: TaskRunDirective,
    expected_shortcut: bool,
) -> None:
    owner = _shortcut_owner()
    runner = TaskRunner(owner)
    shortcut_calls: list[bool] = []
    definition_calls: list[bool] = []
    monkeypatch.setattr(
        task_runner_module,
        "complete_direct_answer",
        lambda *_args: shortcut_calls.append(True) or "shortcut",
    )
    runner._prepare = lambda _inputs: None
    runner._ensure_task_definition = (
        lambda _inputs: definition_calls.append(True) or "definition"
    )
    runner._cleanup = lambda _count: None

    answer = runner.run("oi", None, task_run_directive=directive)

    assert (answer == "shortcut") is expected_shortcut
    assert bool(shortcut_calls) is expected_shortcut
    assert bool(definition_calls) is not expected_shortcut


def test_runtime_cleanup_restores_after_failure() -> None:
    owner = _owner()
    refresh_capability_projection(owner)
    before = owner.allowed_capabilities
    runner = TaskRunner(owner)
    runner._directive_runtime_restore = apply_task_run_directive_runtime(
        owner,
        TaskRunDirective(TaskDirective.READ, DeliberationProfile.SMART, "inspect"),
    )

    def fail(_count: int) -> None:
        raise RuntimeError("planner failure")

    runner._cleanup = fail
    with pytest.raises(RuntimeError, match="planner failure"):
        runner._cleanup_after_run(0)

    assert owner.session.thinking_budget == 777
    assert owner._task_directive_capability_ceiling is None
    assert owner.allowed_capabilities == before


def test_pause_restores_runtime_but_keeps_read_smart_checkpoint() -> None:
    owner = _shortcut_owner()
    owner.session.thinking_budget = 777
    checkpoints: list[dict[str, object]] = []
    owner._save_checkpoint = lambda: checkpoints.append(
        owner.agent_state.task_run_directive.to_checkpoint_dict()
    ) or True
    runner = TaskRunner(owner)
    directive = TaskRunDirective(TaskDirective.READ, DeliberationProfile.SMART, "inspect")

    def prepare(inputs) -> None:
        owner.agent_state.objective = inputs.objective
        owner.agent_state.task_run_directive = inputs.task_run_directive
        runner._directive_runtime_restore = apply_task_run_directive_runtime(
            owner, inputs.task_run_directive
        )

    runner._prepare = prepare
    runner._ensure_task_definition = lambda _inputs: None
    runner._execute = lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt())
    runner._cleanup = lambda _count: None

    answer = runner.run("inspect", None, task_run_directive=directive)

    assert "pausada" in answer
    assert owner.session.thinking_budget == 777
    assert owner._task_directive_capability_ceiling is None
    assert checkpoints == [directive.to_checkpoint_dict()]


def test_sequential_task_has_no_stale_directive_runtime() -> None:
    owner = _owner()
    owner.session.thinking_budget = 777
    refresh_capability_projection(owner)

    first = TaskRunner(owner)
    owner.agent_state.task_run_directive = TaskRunDirective(
        TaskDirective.READ, DeliberationProfile.SMART, "Task A"
    )
    first._directive_runtime_restore = apply_task_run_directive_runtime(
        owner, owner.agent_state.task_run_directive
    )
    first._cleanup = lambda _count: None
    first._cleanup_after_run(0)

    second = TaskRunner(owner)
    observed: list[tuple[object, int, frozenset[str] | None]] = []

    def prepare(inputs) -> None:
        owner.agent_state.task_run_directive = inputs.task_run_directive
        second._directive_runtime_restore = apply_task_run_directive_runtime(
            owner, inputs.task_run_directive
        )
        observed.append(
            (
                owner.agent_state.task_run_directive,
                owner.session.thinking_budget,
                owner._task_directive_capability_ceiling,
            )
        )

    second._prepare = prepare
    second._ensure_task_definition = lambda _inputs: "task B"
    second._cleanup = lambda _count: None

    assert second.run("Task B", None) == "task B"
    directive_b = owner.agent_state.task_run_directive
    assert isinstance(directive_b, TaskRunDirective)
    assert directive_b.directive is TaskDirective.AUTO
    assert directive_b.deliberation_profile is DeliberationProfile.NORMAL
    assert observed == [(directive_b, 777, None)]
    assert owner.session.thinking_budget == 777
    assert owner._task_directive_capability_ceiling is None
