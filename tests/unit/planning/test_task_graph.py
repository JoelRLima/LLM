import time

import pytest

from agent.cancellation import CancellationToken
from agent.llm.contracts import ProviderCapabilities
from agent.planning.task_graph import (
    FailurePolicy,
    NodeState,
    ResourceMode,
    TaskGraph,
    TaskGraphState,
    TaskGraphValidator,
    TaskNode,
    TaskPriority,
    TaskResource,
    task_graph_from_dict,
)
from agent.planning.task_scheduler import TaskGraphScheduler
from agent.runtime.context import RuntimeLimits, TaskExecutionContext, TaskResult, TaskStatus


class FakeGateway:
    provider_name = "fake"
    capabilities = ProviderCapabilities()


class RecordingExecutor:
    def __init__(self, outcomes=None, delay=None):
        self.outcomes = outcomes or {}
        self.delay = delay or {}
        self.contexts = {}

    def execute(self, node, context):
        self.contexts[node.node_id] = context
        time.sleep(self.delay.get(node.node_id, 0))
        return self.outcomes.get(node.node_id, TaskResult(TaskStatus.SUCCEEDED, node.node_id))


def _context():
    return TaskExecutionContext(
        model_gateway=FakeGateway(),
        cancellation=CancellationToken(),
        limits=RuntimeLimits(max_model_concurrency=1),
        permissions=frozenset({"read", "write"}),
    )


def test_graph_rejects_missing_dependencies_and_cycles():
    missing = TaskGraph("x", (TaskNode("a", "a", depends_on=("missing",)),))
    cycle = TaskGraph(
        "x",
        (
            TaskNode("a", "a", depends_on=("b",)),
            TaskNode("b", "b", depends_on=("a",)),
        ),
    )

    assert TaskGraphValidator().validate(missing).valid is False
    assert "ciclo" in TaskGraphValidator().validate(cycle).errors[0]


def test_sequential_scheduler_respects_dependencies_and_ready_priority():
    graph = TaskGraph(
        "diamond",
        (
            TaskNode("root", "root"),
            TaskNode("low", "low", depends_on=("root",), priority=TaskPriority.LOW),
            TaskNode("high", "high", depends_on=("root",), priority=TaskPriority.HIGH),
            TaskNode("end", "end", depends_on=("low", "high")),
        ),
    )

    result = TaskGraphScheduler(RecordingExecutor(), max_workers=1).execute(graph, _context())

    assert result.execution_order == ("root", "high", "low", "end")
    assert result.succeeded is True


def test_failed_node_blocks_dependent_but_not_independent():
    graph = TaskGraph(
        "failure",
        (
            TaskNode("fail", "fail"),
            TaskNode("dependent", "dependent", depends_on=("fail",)),
            TaskNode("independent", "independent"),
        ),
    )
    executor = RecordingExecutor({"fail": TaskResult(TaskStatus.FAILED, error="boom")})

    result = TaskGraphScheduler(executor).execute(graph, _context())

    assert result.states["fail"] == NodeState.FAILED
    assert result.states["dependent"] == NodeState.BLOCKED
    assert result.states["independent"] == NodeState.SUCCEEDED


def test_parallel_scheduler_overlaps_reads_but_serializes_same_file_write():
    reads = TaskGraph(
        "reads",
        (
            TaskNode("r1", "r1", resources=(TaskResource("file.py"),)),
            TaskNode("r2", "r2", resources=(TaskResource("file.py"),)),
        ),
    )
    writes = TaskGraph(
        "writes",
        (
            TaskNode("w1", "w1", resources=(TaskResource("file.py", ResourceMode.WRITE),)),
            TaskNode("w2", "w2", resources=(TaskResource("file.py", ResourceMode.WRITE),)),
        ),
    )
    delayed = RecordingExecutor(delay={"r1": 0.2, "r2": 0.2, "w1": 0.2, "w2": 0.2})

    start = time.monotonic()
    TaskGraphScheduler(delayed, max_workers=2).execute(reads, _context())
    read_duration = time.monotonic() - start
    start = time.monotonic()
    TaskGraphScheduler(delayed, max_workers=2).execute(writes, _context())
    write_duration = time.monotonic() - start

    assert read_duration < 0.35
    assert write_duration >= 0.38


def test_child_contexts_are_isolated_and_results_deterministic():
    graph = TaskGraph("parallel", (TaskNode("slow", "slow"), TaskNode("fast", "fast")))
    executor = RecordingExecutor(delay={"slow": 0.1, "fast": 0.01})
    parent = _context()

    result = TaskGraphScheduler(executor, max_workers=2).execute(graph, parent)

    assert result.execution_order == ("slow", "fast")
    assert executor.contexts["slow"].task_id != executor.contexts["fast"].task_id
    assert executor.contexts["slow"].parent_task_id == parent.task_id


def test_checkpoint_normalizes_running_to_pending():
    graph = TaskGraph("resume", (TaskNode("a", "a"), TaskNode("b", "b", depends_on=("a",))))
    state = TaskGraphState(graph, states={"a": NodeState.SUCCEEDED, "b": NodeState.RUNNING})

    restored = TaskGraphState.from_checkpoint_dict(state.to_checkpoint_dict())

    assert restored.states == {"a": NodeState.SUCCEEDED, "b": NodeState.PENDING}


def test_scheduler_rejects_state_from_other_graph():
    state = TaskGraphState(TaskGraph("one", (TaskNode("a", "a"),)))
    with pytest.raises(ValueError, match="outro TaskGraph"):
        TaskGraphScheduler(RecordingExecutor()).execute(
            TaskGraph("two", (TaskNode("b", "b"),)), _context(), state
        )


def test_continue_policy_runs_after_failed_dependency():
    graph = TaskGraph(
        "continue",
        (
            TaskNode("fail", "fail"),
            TaskNode(
                "cleanup",
                "cleanup",
                depends_on=("fail",),
                failure_policy=FailurePolicy.CONTINUE,
            ),
        ),
    )
    executor = RecordingExecutor({"fail": TaskResult(TaskStatus.FAILED, error="boom")})

    result = TaskGraphScheduler(executor).execute(graph, _context())

    assert result.states["fail"] == NodeState.FAILED
    assert result.states["cleanup"] == NodeState.SUCCEEDED


def test_scheduler_rejects_capability_escalation_before_any_effect():
    graph = TaskGraph(
        "unauthorized",
        (TaskNode("network", "network", capabilities=frozenset({"network"})),),
    )
    executor = RecordingExecutor()

    with pytest.raises(PermissionError, match="network"):
        TaskGraphScheduler(executor).execute(graph, _context())

    assert executor.contexts == {}


def test_unverified_result_has_distinct_graph_state_and_blocks_dependents():
    graph = TaskGraph(
        "unverified",
        (
            TaskNode("change", "change"),
            TaskNode("dependent", "dependent", depends_on=("change",)),
        ),
    )
    executor = RecordingExecutor(
        {"change": TaskResult(TaskStatus.UNVERIFIED, summary="sem validator")}
    )

    result = TaskGraphScheduler(executor).execute(graph, _context())

    assert result.states["change"] == NodeState.UNVERIFIED
    assert result.states["dependent"] == NodeState.BLOCKED


def test_task_graph_parser_rejects_unknown_schema_and_non_list_dependencies():
    with pytest.raises(ValueError, match="schema"):
        task_graph_from_dict({"schema_version": 2, "nodes": []})
    with pytest.raises(ValueError, match="depends_on"):
        task_graph_from_dict(
            {
                "nodes": [
                    {"id": "a", "objective": "a", "depends_on": "b"},
                ]
            }
        )
