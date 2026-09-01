from __future__ import annotations

import json
import threading
import time

from agent.cancellation import CancellationToken
from agent.code.multitask import CodingTaskNodeExecutor
from agent.llm.contracts import ModelResponse, ProviderCapabilities
from agent.planning.task_graph import ResourceMode, TaskGraph, TaskNode, TaskResource
from agent.planning.task_resources import (
    WORKSPACE_RESOURCE,
    effective_resource_claims,
)
from agent.planning.task_scheduler import TaskGraphScheduler
from agent.resources.contracts import normalize_resource_id
from agent.runtime.context import RuntimeLimits, TaskExecutionContext, TaskResult, TaskStatus


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

    assert {claim.name for claim in effective_resource_claims(left)} == {WORKSPACE_RESOURCE}
    assert len(_selected(left, right)) == 1


def test_disjoint_declared_code_task_writes_are_workspace_serialized() -> None:
    assert len(_selected(_change("one", "src/a.py"), _change("two", "src/b.py"))) == 1
    assert len(_selected(_read("one", "src/a.py"), _read("two", "src/a.py"))) == 2


def test_model_generated_disjoint_targets_with_shared_changeset_never_overlap(
    tmp_path,
) -> None:
    for name in ("a.py", "b.py", "shared.py"):
        tmp_path.joinpath(name).write_text("value = 0\n", encoding="utf-8")

    class FakeProposalProvider:
        provider_name = "deterministic-fake"
        capabilities = ProviderCapabilities()

        def __init__(self) -> None:
            self._lock = threading.Lock()
            self._calls = 0
            self._active = 0
            self.max_active = 0

        def complete(self, request):
            del request
            with self._lock:
                self._calls += 1
                call = self._calls
                self._active += 1
                self.max_active = max(self.max_active, self._active)
            try:
                time.sleep(0.05)
                return ModelResponse(
                    content=json.dumps(
                        {
                            "changes": [
                                {
                                    "path": "shared.py",
                                    "kind": "modify",
                                    "content": f"value = {call}\n",
                                }
                            ]
                        }
                    )
                )
            finally:
                with self._lock:
                    self._active -= 1

        def stream(self, request):
            del request
            raise AssertionError("proposal test uses complete")

        def count_tokens(self, text):
            return len(text) // 4

    class ApproveAll:
        def approve(self, preview, assessment):
            del preview, assessment
            return True

    class OverlapRecorder:
        def __init__(self, delegate) -> None:
            self.delegate = delegate
            self._lock = threading.Lock()
            self._active: set[str] = set()
            self.max_active = 0
            self.started: dict[str, float] = {}
            self.finished: dict[str, float] = {}

        def execute(self, node, context):
            with self._lock:
                self._active.add(node.node_id)
                self.max_active = max(self.max_active, len(self._active))
                self.started[node.node_id] = time.monotonic()
            try:
                return self.delegate.execute(node, context)
            finally:
                with self._lock:
                    self.finished[node.node_id] = time.monotonic()
                    self._active.remove(node.node_id)

    provider = FakeProposalProvider()
    context = TaskExecutionContext(
        model_gateway=provider,
        cancellation=CancellationToken(),
        limits=RuntimeLimits(max_model_concurrency=2),
        permissions=frozenset({"read", "write", "validate"}),
    )
    executor = OverlapRecorder(
        CodingTaskNodeExecutor(tmp_path, approver=ApproveAll())
    )
    left = _change("one", "a.py")
    right = _change("two", "b.py")

    result = TaskGraphScheduler(executor, max_workers=2).execute(
        TaskGraph("shared actual footprint", (left, right)),
        context,
    )

    assert result.execution_order == ("one", "two")
    assert result.succeeded is True
    assert executor.max_active == 1
    assert provider.max_active == 1
    assert executor.started["two"] >= executor.finished["one"]
    for node_id in ("one", "two"):
        artifact = result.results[node_id].artifacts[0]
        assert artifact.metadata["affected_files"] == ("shared.py",)


def test_read_write_and_parent_child_paths_conflict() -> None:
    assert len(_selected(_read("read", "src"), _change("write", "src/a.py"))) == 1
    assert len(_selected(_change("parent", "src"), _change("child", "src/a.py"))) == 1


def test_disjoint_analyze_and_review_nodes_retain_real_parallelism() -> None:
    class ParallelRecorder:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self._active = 0
            self.max_active = 0

        def execute(self, node, context):
            del node, context
            with self._lock:
                self._active += 1
                self.max_active = max(self.max_active, self._active)
            try:
                time.sleep(0.05)
                return TaskResult(TaskStatus.SUCCEEDED)
            finally:
                with self._lock:
                    self._active -= 1

    class ReadOnlyGateway:
        provider_name = "unused"
        capabilities = ProviderCapabilities()

    analyze = _read("analyze", "a.py")
    review = TaskNode(
        "review",
        "review b.py",
        capabilities=frozenset({"read", "analyze"}),
        metadata={"action": "review", "targets": ["b.py"]},
    )
    executor = ParallelRecorder()
    context = TaskExecutionContext(
        model_gateway=ReadOnlyGateway(),
        cancellation=CancellationToken(),
        permissions=frozenset({"read", "analyze"}),
    )

    result = TaskGraphScheduler(executor, max_workers=2).execute(
        TaskGraph("read-only concurrency", (analyze, review)),
        context,
    )

    assert result.succeeded is True
    assert executor.max_active == 2


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


def test_canonical_resource_normalization_preserves_conservative_scope() -> None:
    assert normalize_resource_id(r"src\a.py") == "src/a.py"
    assert normalize_resource_id(r"src\pkg\..\a.py") == WORKSPACE_RESOURCE
    assert normalize_resource_id(".") == WORKSPACE_RESOURCE
    assert normalize_resource_id("*") == WORKSPACE_RESOURCE
