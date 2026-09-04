from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.orchestration import task_runner as task_runner_module
from agent.orchestration.task_runner import TaskRunner
from agent.orchestration.task_runner_continuity import TaskInputs
from agent.runtime.event_kinds import RuntimeEventKind
from agent.runtime.task_directives import DeliberationProfile, TaskDirective, TaskRunDirective


def _owner(directive: TaskRunDirective | None) -> tuple[SimpleNamespace, list[tuple[str, dict[str, object]]]]:
    events: list[tuple[str, dict[str, object]]] = []
    state = SimpleNamespace(
        objective=directive.subject if directive is not None else "",
        task_run_directive=directive,
    )

    def reset_task_state(objective: str) -> None:
        state.objective = objective
        state.task_run_directive = None

    owner = SimpleNamespace(
        agent_state=state,
        session=SimpleNamespace(thinking_budget=256),
        _emit=lambda kind, data=None: events.append((kind, dict(data or {}))),
        _reset_task_state=reset_task_state,
        _count_metrics_lines=lambda: 0,
        task_policy=None,
        _task_directive_capability_ceiling=None,
    )
    return owner, events


def test_fresh_read_smart_emits_one_bounded_selection_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directive = TaskRunDirective(TaskDirective.READ, DeliberationProfile.SMART, "read source")
    owner, events = _owner(None)
    monkeypatch.setattr(task_runner_module, "initialize_task_progression", lambda *_args, **_kwargs: None)
    runner = TaskRunner(owner)

    runner._prepare(TaskInputs("read source", False, 0, task_run_directive=directive))

    assert events == [
        (
            RuntimeEventKind.TASK_DIRECTIVE_SELECTED.value,
            {
                "directive": "read",
                "deliberation_profile": "smart",
                "resumed": False,
            },
        )
    ]


def test_fresh_auto_normal_emits_one_bounded_selection_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directive = TaskRunDirective(TaskDirective.AUTO, DeliberationProfile.NORMAL, "default task")
    owner, events = _owner(None)
    monkeypatch.setattr(task_runner_module, "initialize_task_progression", lambda *_args, **_kwargs: None)
    runner = TaskRunner(owner)

    runner._prepare(TaskInputs("default task", False, 0, task_run_directive=directive))

    assert events == [
        (
            RuntimeEventKind.TASK_DIRECTIVE_SELECTED.value,
            {
                "directive": "auto",
                "deliberation_profile": "normal",
                "resumed": False,
            },
        )
    ]


def test_resumed_read_smart_emits_selection_event_with_resumed_true() -> None:
    directive = TaskRunDirective(TaskDirective.READ, DeliberationProfile.SMART, "read source")
    owner, events = _owner(directive)
    runner = TaskRunner(owner)

    runner._prepare(TaskInputs("read source", True, 0, task_run_directive=directive))

    assert events == [
        (
            RuntimeEventKind.TASK_DIRECTIVE_SELECTED.value,
            {
                "directive": "read",
                "deliberation_profile": "smart",
                "resumed": True,
            },
        )
    ]


def test_selection_event_failure_does_not_change_applied_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directive = TaskRunDirective(TaskDirective.READ, DeliberationProfile.SMART, "read source")
    owner, _events = _owner(None)

    def fail_emit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("observation sink unavailable")

    owner._emit = fail_emit
    monkeypatch.setattr(task_runner_module, "initialize_task_progression", lambda *_args, **_kwargs: None)
    runner = TaskRunner(owner)

    runner._prepare(TaskInputs("read source", False, 0, task_run_directive=directive))

    assert owner.session.thinking_budget == 1024
    assert runner._directive_runtime_restore is not None
