from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

import pytest

from agent.cancellation import CancellationToken
from agent.orchestration.task_runner_continuity import (
    ExplicitResumeRefused,
    resolve_inputs,
)
from agent.planning.task_completion import initialize_task_progression
from agent.runtime.budget import TaskBudgetLedger
from agent.runtime.task_directives import (
    DeliberationProfile,
    TaskDirective,
    TaskRunDirective,
)
from agent.runtime.task_execution_context import TaskExecutionOwnershipMixin
from agent.state import AgentState


def _runner() -> SimpleNamespace:
    return SimpleNamespace(orchestrator=SimpleNamespace())


def test_fresh_resolution_defaults_to_auto_normal_and_preserves_subject() -> None:
    inputs = resolve_inputs(_runner(), "Analyze repo", 0)

    assert inputs is not None
    task_run_directive = inputs.task_run_directive
    assert task_run_directive is not None
    assert inputs.objective == "Analyze repo"
    assert task_run_directive.directive is TaskDirective.AUTO
    assert task_run_directive.deliberation_profile is DeliberationProfile.NORMAL
    assert task_run_directive.subject == "Analyze repo"


def test_fresh_plan_resolution_uses_canonical_objective_and_keeps_subject() -> None:
    subject = "Refactor parser.py"
    directive = TaskRunDirective(TaskDirective.PLAN, DeliberationProfile.CAUTIOUS, subject)

    inputs = resolve_inputs(_runner(), subject, 0, task_run_directive=directive)
    assert inputs is not None
    assert inputs.objective == directive.canonical_objective()
    assert inputs.task_run_directive is directive

    state = AgentState()
    orchestrator = SimpleNamespace(agent_state=state)
    state.task_run_directive = directive
    initialize_task_progression(orchestrator, inputs.objective, plan_only=True)

    assert state.objective == directive.canonical_objective()
    assert state.task_run_directive.subject == subject
    assert state.task_semantics.objective == directive.canonical_objective()
    assert state.task_semantics.requested_effects == ()
    assert state.task_semantics.obligations == ()
    assert state.task_semantics.effect_authority.proposal_only is True
    assert state.task_semantics.candidate_effect_intents


@pytest.mark.parametrize(
    "directive",
    [
        TaskRunDirective(TaskDirective.READ, DeliberationProfile.SMART, "Read source.py"),
        TaskRunDirective(TaskDirective.PLAN, DeliberationProfile.CAUTIOUS, "Refactor parser.py"),
    ],
)
def test_w11_checkpoint_round_trip_preserves_typed_value(
    directive: TaskRunDirective,
) -> None:
    state = AgentState()
    state.objective = directive.canonical_objective()
    state.task_run_directive = directive
    state.initialize_task_semantics(
        state.objective,
        plan_only=directive.directive is TaskDirective.PLAN,
    )
    checkpoint = state.to_checkpoint_dict()

    restored = AgentState()
    restored.from_checkpoint_dict(checkpoint)

    assert restored.task_run_directive == directive
    assert restored.task_run_directive.canonical_objective() == restored.objective


def test_old_checkpoint_without_w11_field_gets_safe_compatibility_default() -> None:
    state = AgentState()
    state.objective = "old W10 objective"
    state.initialize_task_semantics(state.objective)
    checkpoint = state.to_checkpoint_dict()
    assert "task_run_directive" not in checkpoint

    restored = AgentState()
    restored.from_checkpoint_dict(checkpoint)

    assert restored.task_run_directive is not None
    assert restored.task_run_directive.directive is TaskDirective.AUTO
    assert restored.task_run_directive.deliberation_profile is DeliberationProfile.NORMAL
    assert restored.task_run_directive.subject == state.objective


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(extra="x"),
        lambda value: value.update(directive="unknown"),
        lambda value: value.update(deliberation_profile="unknown"),
        lambda value: value.update(schema_version=True),
        lambda value: value.update(subject="different"),
    ],
)
def test_malformed_present_w11_field_fails_closed(
    mutation: Callable[[dict[str, object]], None],
) -> None:
    state = AgentState()
    state.objective = "old objective"
    state.initialize_task_semantics(state.objective)
    checkpoint = state.to_checkpoint_dict()
    checkpoint["task_run_directive"] = TaskRunDirective(
        TaskDirective.AUTO,
        DeliberationProfile.NORMAL,
        state.objective,
    ).to_checkpoint_dict()
    mutation(checkpoint["task_run_directive"])

    restored = AgentState()
    with pytest.raises(ValueError):
        restored.from_checkpoint_dict(checkpoint)
    assert restored.task_run_directive is None


def test_plan_checkpoint_tampering_fails_closed() -> None:
    first = TaskRunDirective(TaskDirective.PLAN, DeliberationProfile.NORMAL, "Subject A")
    state = AgentState()
    state.objective = first.canonical_objective()
    state.task_run_directive = first
    state.initialize_task_semantics(state.objective, plan_only=True)
    checkpoint = state.to_checkpoint_dict()
    checkpoint["task_run_directive"]["subject"] = "Subject B"

    with pytest.raises(ValueError):
        AgentState().from_checkpoint_dict(checkpoint)


def test_resume_directive_override_is_rejected_before_checkpoint_load() -> None:
    loaded = []

    class Runner:
        orchestrator = SimpleNamespace(
            _load_checkpoint=lambda: loaded.append(True),
        )

    directive = TaskRunDirective(TaskDirective.READ, DeliberationProfile.SMART, "read source.py")
    with pytest.raises(ExplicitResumeRefused) as error:
        resolve_inputs(
            Runner(),
            None,
            0,
            explicit_resume=True,
            task_run_directive=directive,
        )

    assert error.value.reason_code == "TASK_RESUME_DIRECTIVE_OVERRIDE_NOT_ALLOWED"
    assert loaded == []


def test_plan_checkpoint_with_executable_plan_is_rejected() -> None:
    directive = TaskRunDirective(TaskDirective.PLAN, DeliberationProfile.NORMAL, "Subject")
    state = AgentState()
    state.objective = directive.canonical_objective()
    state.task_run_directive = directive
    state.initialize_task_semantics(state.objective, plan_only=True)
    state.set_plan([{"tool": "echo", "args": {"text": "must not resume"}}])
    checkpoint = state.to_checkpoint_dict()

    with pytest.raises(ValueError, match="PLAN directive"):
        AgentState().from_checkpoint_dict(checkpoint)


def test_canonical_reset_clears_previous_w11_directive() -> None:
    class Owner(TaskExecutionOwnershipMixin):
        pass

    owner: Any = Owner()
    owner.task_budget = TaskBudgetLedger()
    owner.agent_state = AgentState(budget_ledger=owner.task_budget)
    owner.agent_state.task_run_directive = TaskRunDirective(
        TaskDirective.READ,
        DeliberationProfile.CAUTIOUS,
        "Task A",
    )
    owner.context_manager = SimpleNamespace(_cached_project_context=object())
    owner.workspace = SimpleNamespace(
        restore_points=[],
        created_files=set(),
        discard_transactions=lambda: None,
    )
    owner._planning_context = object()
    owner._task_failed = True
    owner._cancelled = True
    owner.cancellation_token = CancellationToken()
    owner._task_execution_context = None

    owner._reset_task_state("Task B")

    assert owner.agent_state.task_run_directive is None
    assert owner.agent_state.objective == "Task B"
