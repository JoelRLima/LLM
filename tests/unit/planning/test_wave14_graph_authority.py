import pytest

from agent.cancellation import CancellationToken
from agent.llm.contracts import ProviderCapabilities
from agent.planning.graph_authority import (
    GraphAuthorityError,
    derive_graph_requirements,
    preflight_graph_capabilities,
)
from agent.planning.task_graph import TaskGraph, TaskNode
from agent.planning.task_scheduler import TaskGraphScheduler
from agent.runtime.context import TaskExecutionContext, TaskResult, TaskStatus


class _Gateway:
    provider_name = "wave14"
    capabilities = ProviderCapabilities()


def _context(permissions):
    return TaskExecutionContext(
        model_gateway=_Gateway(),
        cancellation=CancellationToken(),
        permissions=frozenset(permissions),
    )


def test_trusted_action_ignores_fake_node_process_claim() -> None:
    graph = TaskGraph(
        "inspect",
        (
            TaskNode(
                "inspect",
                "inspect",
                capabilities=frozenset({"read", "analyze", "process"}),
                metadata={"action": "analyze", "targets": ["module.py"]},
            ),
        ),
    )

    requirements = preflight_graph_capabilities(graph, {"read", "analyze"})
    assert requirements.required_capabilities == {"read", "analyze"}
    assert "process" not in requirements.required_capabilities


def test_trusted_include_tests_derives_process_before_scheduling() -> None:
    graph = TaskGraph(
        "modify",
        (
            TaskNode(
                "modify",
                "modify",
                metadata={
                    "action": "modify",
                    "targets": ["module.py"],
                    "include_tests": True,
                },
            ),
        ),
    )

    with pytest.raises(GraphAuthorityError, match="process"):
        preflight_graph_capabilities(graph, {"read", "write", "validate"})


def test_unknown_action_fails_closed() -> None:
    graph = TaskGraph(
        "unknown",
        (TaskNode("unknown", "unknown", metadata={"action": "invented"}),),
    )
    with pytest.raises(GraphAuthorityError, match="GRAPH_UNKNOWN_ACTION"):
        derive_graph_requirements(graph)


def test_scheduler_preflights_before_executor() -> None:
    called: list[str] = []

    class Executor:
        def execute(self, node, context):
            called.append(node.node_id)
            return TaskResult(TaskStatus.SUCCEEDED)

    graph = TaskGraph(
        "modify",
        (
            TaskNode(
                "modify",
                "modify",
                metadata={"action": "modify", "targets": ["module.py"]},
            ),
        ),
    )
    with pytest.raises(GraphAuthorityError, match="write"):
        TaskGraphScheduler(Executor()).execute(graph, _context({"read"}))
    assert called == []
