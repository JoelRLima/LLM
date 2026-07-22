"""Validated task graph with executable dependencies and resources."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Dict

from agent.planning.task_graph_validation import GraphValidationReport, TaskGraphValidator

__all__ = [
    "FailurePolicy", "GraphValidationReport", "NodeState", "ResourceMode", "TaskGraph",
    "TaskGraphState", "TaskGraphValidator", "TaskNode", "TaskPriority", "TaskResource",
    "task_graph_from_dict", "task_graph_from_macro_plan", "topological_nodes",
]


class TaskPriority(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class ResourceMode(str, Enum):
    READ = "read"
    WRITE = "write"


@dataclass(frozen=True)
class TaskResource:
    name: str
    mode: ResourceMode = ResourceMode.READ


class FailurePolicy(str, Enum):
    BLOCK_DEPENDENTS = "block_dependents"
    CONTINUE = "continue"
    FAIL_FAST = "fail_fast"


@dataclass(frozen=True)
class TaskNode:
    node_id: str
    objective: str
    depends_on: tuple[str, ...] = ()
    priority: TaskPriority = TaskPriority.MEDIUM
    resources: tuple[TaskResource, ...] = ()
    capabilities: frozenset[str] = frozenset()
    failure_policy: FailurePolicy = FailurePolicy.BLOCK_DEPENDENTS
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskGraph:
    objective: str
    nodes: tuple[TaskNode, ...]
    schema_version: int = 1

    def by_id(self) -> Dict[str, TaskNode]:
        return {node.node_id: node for node in self.nodes}


class NodeState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    UNVERIFIED = "unverified"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"'{field_name}' deve ser uma lista de strings não vazias.")
    return tuple(value)


def _resources_from_raw(value: Any) -> tuple[TaskResource, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("'resources' deve ser uma lista.")
    resources: list[TaskResource] = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("Recurso do TaskGraph deve possuir nome string.")
        resources.append(TaskResource(item["name"], ResourceMode(str(item.get("mode", "read")))))
    return tuple(resources)


def _metadata_from_raw(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("'metadata' deve ser um objeto.")
    return dict(value)


def _node_from_raw(raw: Any, error_context: str = "TaskGraph") -> TaskNode:
    if not isinstance(raw, dict):
        raise ValueError(f"Nó inválido no {error_context}.")
    try:
        if not isinstance(raw.get("id"), str) or not isinstance(raw.get("objective"), str):
            raise ValueError("Nó exige id e objective strings.")
        return TaskNode(
            node_id=raw["id"], objective=raw["objective"],
            depends_on=_string_tuple(raw.get("depends_on"), "depends_on"),
            priority=TaskPriority[str(raw.get("priority", "medium")).upper()],
            resources=_resources_from_raw(raw.get("resources")),
            capabilities=frozenset(_string_tuple(raw.get("capabilities"), "capabilities")),
            failure_policy=FailurePolicy(str(raw.get("failure_policy", FailurePolicy.BLOCK_DEPENDENTS.value))),
            metadata=_metadata_from_raw(raw.get("metadata")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and "depends_on" in str(exc):
            raise
        raise ValueError(f"Nó inválido no {error_context}.") from exc


@dataclass
class TaskGraphState:
    graph: TaskGraph
    states: Dict[str, NodeState] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for node in self.graph.nodes:
            self.states.setdefault(node.node_id, NodeState.PENDING)

    def to_checkpoint_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "objective": self.graph.objective,
            "nodes": [_node_to_dict(node) for node in self.graph.nodes],
            "states": {node_id: state.value for node_id, state in self.states.items()},
            "errors": dict(self.errors),
        }

    @classmethod
    def from_checkpoint_dict(cls, data: Dict[str, Any]) -> "TaskGraphState":
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("Checkpoint do TaskGraph inválido ou não suportado.")
        raw_nodes = data.get("nodes")
        if not isinstance(raw_nodes, list):
            raise ValueError("Checkpoint do TaskGraph sem nós válidos.")
        nodes = [_node_from_raw(raw, "checkpoint do TaskGraph") for raw in raw_nodes]
        graph = TaskGraph(str(data.get("objective", "")), tuple(nodes))
        _ensure_valid(graph, "TaskGraph inválido no checkpoint")
        raw_states, raw_errors = data.get("states") or {}, data.get("errors") or {}
        if not isinstance(raw_states, dict) or not isinstance(raw_errors, dict):
            raise ValueError("Estados ou erros inválidos no checkpoint do TaskGraph.")
        states = {
            node.node_id: _restored_state(raw_states.get(node.node_id)) for node in nodes
        }
        return cls(graph, states, {str(key): str(value) for key, value in raw_errors.items()})


def _node_to_dict(node: TaskNode) -> Dict[str, Any]:
    return {
        "id": node.node_id, "objective": node.objective,
        "depends_on": list(node.depends_on), "priority": node.priority.name.lower(),
        "resources": [{"name": item.name, "mode": item.mode.value} for item in node.resources],
        "capabilities": sorted(node.capabilities), "failure_policy": node.failure_policy.value,
        "metadata": node.metadata,
    }


def _restored_state(raw: Any) -> NodeState:
    state = NodeState(str(raw or NodeState.PENDING.value))
    return NodeState.PENDING if state == NodeState.RUNNING else state


def _ensure_valid(graph: TaskGraph, prefix: str = "TaskGraph inválido") -> None:
    report = TaskGraphValidator().validate(graph)
    if not report.valid:
        raise ValueError(prefix + ": " + "; ".join(report.errors))


def task_graph_from_macro_plan(macro_plan: Any) -> TaskGraph:
    priorities = {"low": TaskPriority.LOW, "medium": TaskPriority.MEDIUM, "high": TaskPriority.HIGH, "critical": TaskPriority.CRITICAL}
    nodes = tuple(TaskNode(
        node_id=str(step.id), objective=str(step.goal), depends_on=tuple(step.depends_on),
        priority=priorities.get(str(getattr(step.priority, "value", step.priority)), TaskPriority.MEDIUM),
        metadata={"title": step.title, "estimated_tools": list(step.estimated_tools)},
    ) for step in macro_plan.steps)
    graph = TaskGraph(str(macro_plan.objective), nodes)
    _ensure_valid(graph, "MacroPlan inválido")
    return graph


def task_graph_from_dict(data: Any, objective: str = "") -> TaskGraph:
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise ValueError("TaskGraph deve ser um objeto com lista 'nodes'.")
    if data.get("schema_version", 1) != 1:
        raise ValueError("Versão de schema do TaskGraph não suportada.")
    graph = TaskGraph(str(data.get("objective") or objective), tuple(_node_from_raw(raw) for raw in data["nodes"]))
    _ensure_valid(graph)
    return graph


def topological_nodes(graph: TaskGraph) -> tuple[TaskNode, ...]:
    _ensure_valid(graph)
    remaining = graph.by_id()
    completed: set[str] = set()
    original_order = {node.node_id: index for index, node in enumerate(graph.nodes)}
    result: list[TaskNode] = []
    while remaining:
        ready = [node for node in remaining.values() if set(node.depends_on).issubset(completed)]
        ready.sort(key=lambda node: (-int(node.priority), original_order[node.node_id]))
        node = ready[0]
        result.append(node)
        completed.add(node.node_id)
        del remaining[node.node_id]
    return tuple(result)
