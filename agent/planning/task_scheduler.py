"""Bounded, resource-aware local task-graph scheduler."""

from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
from typing import Dict, Optional, Protocol

from agent.planning.task_graph import (
    FailurePolicy,
    NodeState,
    TaskGraph,
    TaskGraphState,
    TaskGraphValidator,
    TaskNode,
    TaskResource,
)
from agent.planning.task_resources import (
    effective_resource_claims,
    resource_claims_conflict,
)
from agent.resources.contracts import ResourceAccess
from agent.runtime.budget import BudgetExhausted
from agent.runtime.context import TaskExecutionContext, TaskResult, TaskStatus


class TaskNodeExecutor(Protocol):
    def execute(self, node: TaskNode, context: TaskExecutionContext) -> TaskResult: ...


@dataclass(frozen=True)
class GraphExecutionResult:
    states: Dict[str, NodeState]
    results: Dict[str, TaskResult]
    execution_order: tuple[str, ...]
    errors: Dict[str, str] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return bool(self.states) and all(state == NodeState.SUCCEEDED for state in self.states.values())


def resources_conflict(left: tuple[TaskResource, ...], right: tuple[TaskResource, ...]) -> bool:
    return resource_claims_conflict(
        tuple(ResourceAccess(item.name, item.mode) for item in left),
        tuple(ResourceAccess(item.name, item.mode) for item in right),
    )


class TaskGraphScheduler:
    def __init__(self, executor: TaskNodeExecutor, max_workers: int = 1) -> None:
        self.executor = executor
        self.max_workers = max(1, max_workers)

    @staticmethod
    def _sort_ready(graph: TaskGraph, ids: list[str]) -> list[TaskNode]:
        order = {node.node_id: index for index, node in enumerate(graph.nodes)}
        by_id = graph.by_id()
        return [by_id[node_id] for node_id in sorted(ids, key=lambda item: (-int(by_id[item].priority), order[item]))]

    def _select_batch(self, ready: list[TaskNode]) -> list[TaskNode]:
        selected: list[TaskNode] = []
        for node in ready:
            if len(selected) >= self.max_workers:
                break
            node_resources = effective_resource_claims(node)
            if not any(
                resource_claims_conflict(node_resources, effective_resource_claims(existing))
                for existing in selected
            ):
                selected.append(node)
        return selected or ready[:1]

    @staticmethod
    def _block_failed_dependencies(state: TaskGraphState) -> None:
        failures = {NodeState.UNVERIFIED, NodeState.FAILED, NodeState.BLOCKED, NodeState.CANCELLED}
        changed = True
        while changed:
            changed = False
            for node in state.graph.nodes:
                failed = [dep for dep in node.depends_on if state.states[dep] in failures]
                if state.states[node.node_id] == NodeState.PENDING and failed and node.failure_policy != FailurePolicy.CONTINUE:
                    state.states[node.node_id] = NodeState.BLOCKED
                    state.errors[node.node_id] = "Dependência falhou: " + ", ".join(failed)
                    changed = True

    def execute(
        self,
        graph: TaskGraph,
        parent_context: TaskExecutionContext,
        state: Optional[TaskGraphState] = None,
    ) -> GraphExecutionResult:
        """Execute a graph; ``state`` is the explicit graph resume boundary.

        The root task checkpoint does not infer or duplicate graph progress.
        Callers that support graph resume must persist and pass the existing
        ``TaskGraphState`` owner back into this method.
        """

        self._validate(graph, parent_context)
        current = state or TaskGraphState(graph)
        if current.graph != graph:
            raise ValueError("Estado pertence a outro TaskGraph.")
        results: Dict[str, TaskResult] = {}
        order: list[str] = []
        fail_fast = False
        while self._active(current):
            if parent_context.cancellation.cancelled or fail_fast:
                self._cancel_pending(current)
                break
            self._block_failed_dependencies(current)
            ready = self._ready_nodes(graph, current)
            if not ready:
                break
            batch = self._select_batch(ready)
            batch_results = self._run_batch(batch, current, parent_context)
            fail_fast = self._record_batch(batch, batch_results, current, results, order)
        return GraphExecutionResult(dict(current.states), results, tuple(order), dict(current.errors))

    @staticmethod
    def _validate(graph: TaskGraph, context: TaskExecutionContext) -> None:
        validation = TaskGraphValidator().validate(graph)
        if not validation.valid:
            raise ValueError("TaskGraph inválido: " + "; ".join(validation.errors))
        if len(graph.nodes) > context.limits.max_steps:
            raise ValueError(f"TaskGraph excede o limite de {context.limits.max_steps} nós.")
        unauthorized = {
            node.node_id: sorted(node.capabilities - context.permissions)
            for node in graph.nodes if not node.capabilities.issubset(context.permissions)
        }
        if unauthorized:
            detail = "; ".join(f"{node_id}: {', '.join(items)}" for node_id, items in unauthorized.items())
            raise PermissionError("TaskGraph solicita capacidades não autorizadas: " + detail)

    @staticmethod
    def _active(state: TaskGraphState) -> bool:
        return any(status in {NodeState.PENDING, NodeState.RUNNING} for status in state.states.values())

    @staticmethod
    def _cancel_pending(state: TaskGraphState) -> None:
        for node_id, status in tuple(state.states.items()):
            if status == NodeState.PENDING:
                state.states[node_id] = NodeState.CANCELLED

    def _ready_nodes(self, graph: TaskGraph, state: TaskGraphState) -> list[TaskNode]:
        ready_ids = [node.node_id for node in graph.nodes if self._is_ready(node, state)]
        return self._sort_ready(graph, ready_ids)

    @staticmethod
    def _is_ready(node: TaskNode, state: TaskGraphState) -> bool:
        if state.states[node.node_id] != NodeState.PENDING:
            return False
        accepted = {NodeState.SUCCEEDED}
        if node.failure_policy == FailurePolicy.CONTINUE:
            accepted |= {NodeState.UNVERIFIED, NodeState.FAILED, NodeState.BLOCKED, NodeState.CANCELLED}
        return all(state.states[dependency] in accepted for dependency in node.depends_on)

    def _run_batch(
        self, batch: list[TaskNode], state: TaskGraphState, parent: TaskExecutionContext
    ) -> Dict[str, TaskResult]:
        results: Dict[str, TaskResult] = {}
        dispatch_batch = batch
        policy = getattr(parent, "task_policy", None)
        if policy is not None:
            admission = policy.admit_work_units(len(batch))
            dispatch_batch = batch[: admission.admitted_units]
            if admission.denied:
                dispatch_batch = []
            synthetic_status = (
                TaskStatus.CANCELLED
                if admission.decision.value == "cancelled"
                else TaskStatus.BLOCKED
            )
            for node in batch[len(dispatch_batch) :]:
                results[node.node_id] = TaskResult(
                    synthetic_status,
                    error=admission.message or admission.reason_code,
                )
        if not dispatch_batch:
            return results
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(dispatch_batch)) as pool:
            futures: Dict[concurrent.futures.Future[TaskResult], TaskNode] = {}
            for node in dispatch_batch:
                state.states[node.node_id] = NodeState.RUNNING
                child = parent.child(node.node_id, permissions=frozenset(node.capabilities))
                child.emit("task_node_started", {"objective": node.objective})
                futures[pool.submit(self.executor.execute, node, child)] = node
            for future in concurrent.futures.as_completed(futures):
                node = futures[future]
                results[node.node_id] = self._future_result(future)
        return results

    @staticmethod
    def _future_result(future: concurrent.futures.Future[TaskResult]) -> TaskResult:
        try:
            return future.result()
        except BudgetExhausted:
            raise
        except Exception as exc:
            return TaskResult(TaskStatus.FAILED, error=str(exc))

    @staticmethod
    def _record_batch(
        batch: list[TaskNode], batch_results: Dict[str, TaskResult], state: TaskGraphState,
        results: Dict[str, TaskResult], order: list[str],
    ) -> bool:
        fail_fast = False
        status_map = {
            TaskStatus.SUCCEEDED: NodeState.SUCCEEDED,
            TaskStatus.UNVERIFIED: NodeState.UNVERIFIED,
            TaskStatus.BLOCKED: NodeState.BLOCKED,
            TaskStatus.CANCELLED: NodeState.CANCELLED,
            TaskStatus.FAILED: NodeState.FAILED,
        }
        for node in batch:
            result = batch_results[node.node_id]
            results[node.node_id] = result
            order.append(node.node_id)
            state.states[node.node_id] = status_map[result.status]
            if result.status not in {TaskStatus.SUCCEEDED, TaskStatus.CANCELLED}:
                state.errors[node.node_id] = result.error or result.summary or result.status.value
            if result.status == TaskStatus.FAILED and node.failure_policy == FailurePolicy.FAIL_FAST:
                fail_fast = True
        return fail_fast
