from __future__ import annotations

from agent.planning.task_graph import ResourceMode, TaskNode, TaskResource
from agent.planning.task_resources import (
    WORKSPACE_RESOURCE,
    effective_resource_claims,
    normalize_resource_name,
)
from agent.planning.task_scheduler import TaskGraphScheduler


class _NoopExecutor:
    def execute(self, node, context):  # pragma: no cover - selection only
        del node, context
        raise AssertionError("selection test must not execute a node")


def _change(node_id: str, target: str, *, declared=()) -> TaskNode:
    return TaskNode(
        node_id,
        node_id,
        resources=tuple(declared),
        capabilities=frozenset({"read", "write", "validate"}),
        metadata={"action": "modify", "targets": [target]},
    )


def _read(node_id: str, target: str) -> TaskNode:
    return TaskNode(
        node_id,
        node_id,
        capabilities=frozenset({"read", "analyze"}),
        metadata={"action": "analyze", "targets": [target]},
    )


def _selected(*nodes: TaskNode) -> list[TaskNode]:
    return TaskGraphScheduler(_NoopExecutor(), max_workers=2)._select_batch(list(nodes))


def test_same_file_writes_without_resources_are_serialized() -> None:
    assert len(_selected(_change("one", "src/a.py"), _change("two", "src/a.py"))) == 1


def test_false_disjoint_resource_claims_cannot_gain_concurrency() -> None:
    declared = (TaskResource("claimed-a.py", ResourceMode.WRITE),)
    left = _change("one", "src/shared.py", declared=declared)
    right = _change(
        "two",
        "src/shared.py",
        declared=(TaskResource("claimed-b.py", ResourceMode.WRITE),),
    )

    assert {claim.name for claim in effective_resource_claims(left)} == {"src/shared.py"}
    assert len(_selected(left, right)) == 1


def test_disjoint_trusted_writes_and_overlapping_reads_can_parallelize() -> None:
    assert len(_selected(_change("one", "src/a.py"), _change("two", "src/b.py"))) == 2
    assert len(_selected(_read("one", "src/a.py"), _read("two", "src/a.py"))) == 2


def test_read_write_and_parent_child_paths_conflict() -> None:
    assert len(_selected(_read("read", "src"), _change("write", "src/a.py"))) == 1
    assert len(_selected(_change("parent", "src"), _change("child", "src/a.py"))) == 1


def test_unknown_mutating_footprint_becomes_workspace_write() -> None:
    unknown = TaskNode(
        "unknown",
        "unknown mutator",
        resources=(TaskResource("claimed-a.py", ResourceMode.WRITE),),
        capabilities=frozenset({"write"}),
    )
    concrete = _change("concrete", "src/a.py")

    assert effective_resource_claims(unknown)[0].name == WORKSPACE_RESOURCE
    assert len(_selected(unknown, concrete)) == 1


def test_resource_normalization_handles_slashes_root_and_workspace_wildcard() -> None:
    assert normalize_resource_name(r"src\pkg\..\a.py") == "src/a.py"
    assert normalize_resource_name(".") == WORKSPACE_RESOURCE
    assert normalize_resource_name("*") == WORKSPACE_RESOURCE
