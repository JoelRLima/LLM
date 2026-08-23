from __future__ import annotations

import pytest

from agent.cancellation import CancellationToken
from agent.code import multitask as multitask_module
from agent.code.multitask import CodingTaskNodeExecutor
from agent.code.validation_process import CommandSpec, ProcessRunner, ValidationStatus
from agent.planning.task_graph import TaskGraph, TaskNode
from agent.planning.task_scheduler import TaskGraphScheduler
from agent.runtime.budget import BudgetExhausted
from agent.runtime.context import RuntimeLimits, TaskExecutionContext, TaskResult, TaskStatus


class _Gateway:
    provider_name = "scripted"
    capabilities = None


def _context(*, permissions: frozenset[str] = frozenset({"read", "write", "validate"})) -> TaskExecutionContext:
    return TaskExecutionContext(
        model_gateway=_Gateway(),
        cancellation=CancellationToken(),
        limits=RuntimeLimits(max_model_calls=1, max_task_tool_calls=1),
        permissions=permissions,
    )


def test_child_model_and_tool_budget_are_parent_owned() -> None:
    parent = _context()
    child = parent.child("nested")

    assert parent.consume_model_call() == 1
    with pytest.raises(BudgetExhausted):
        child.consume_model_call()
    assert parent.reserve_tool_call() == 1
    with pytest.raises(BudgetExhausted):
        child.reserve_tool_call()


def test_parent_cancellation_reaches_derived_child() -> None:
    parent = _context()
    child = parent.child("nested")

    assert child.cancellation.cancelled is False
    parent.cancellation.cancel()

    assert child.cancellation.cancelled is True


def test_child_capabilities_are_narrowed_and_widening_is_rejected() -> None:
    parent = _context(permissions=frozenset({"read", "validate"}))

    assert parent.child("read-only", permissions=frozenset({"read"})).permissions == {"read"}
    with pytest.raises(PermissionError, match="write"):
        parent.child("escalated", permissions=frozenset({"read", "write"}))


def test_editor_code_action_requires_validation_not_general_process(monkeypatch, tmp_path) -> None:
    captured: list[TaskExecutionContext] = []

    class _Workflow:
        def __init__(self, _root, context, **_kwargs):
            captured.append(context)

        def change(self, *_args, **_kwargs):
            return TaskResult(TaskStatus.SUCCEEDED, summary="validated")

    monkeypatch.setattr(multitask_module, "CodingWorkflowService", _Workflow)
    executor = CodingTaskNodeExecutor(tmp_path)
    context = _context(permissions=frozenset({"read", "write", "validate"}))

    result = executor.execute(
        TaskNode(
            "modify",
            "modify",
            capabilities=frozenset({"read", "write", "validate"}),
            metadata={"action": "modify", "targets": ["a.py"]},
        ),
        context,
    )

    assert result.status is TaskStatus.SUCCEEDED
    assert captured[0].permissions == {"read", "write", "validate"}
    assert "process" not in captured[0].permissions

    blocked = executor.execute(
        TaskNode(
            "modify-tests",
            "modify with tests",
            capabilities=frozenset({"read", "write", "validate", "process"}),
            metadata={
                "action": "modify",
                "targets": ["a.py"],
                "include_tests": True,
            },
        ),
        context,
    )
    assert blocked.status is TaskStatus.BLOCKED
    assert "process" in (blocked.error or "")


def test_bounded_validation_provider_runs_without_process_permission(tmp_path) -> None:
    runner = ProcessRunner(tmp_path, cancellation=CancellationToken())
    result = runner.run(
        CommandSpec(
            "python-version",
            ("python", "-c", "print('ok')"),
            cwd=".",
            timeout_seconds=5,
        )
    )

    assert result.status is ValidationStatus.PASSED


def test_nested_multitask_nodes_share_the_same_ownership_tree() -> None:
    parent = _context(permissions=frozenset({"read"}))
    contexts: dict[str, TaskExecutionContext] = {}

    class _Executor:
        def execute(self, node, context):
            contexts[node.node_id] = context
            return TaskResult(TaskStatus.SUCCEEDED, summary=node.node_id)

    graph = TaskGraph(
        "nested",
        (
            TaskNode("one", "one", capabilities=frozenset({"read"})),
            TaskNode("two", "two", capabilities=frozenset({"read"})),
        ),
    )
    result = TaskGraphScheduler(_Executor(), max_workers=2).execute(graph, parent)

    assert result.succeeded is True
    assert {item.parent_task_id for item in contexts.values()} == {parent.task_id}
    assert {item.budget_ledger for item in contexts.values()} == {parent.budget_ledger}
    assert {item.model_gate for item in contexts.values()} == {parent.model_gate}
    assert {item.process_gate for item in contexts.values()} == {parent.process_gate}
    assert {item.cancellation for item in contexts.values()} == {parent.cancellation}
