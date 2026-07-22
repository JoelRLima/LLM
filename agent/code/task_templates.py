"""Templates determinísticos de TaskGraph para operações comuns de código."""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Sequence

from agent.planning.task_graph import (
    ResourceMode,
    TaskGraph,
    TaskNode,
    TaskPriority,
    TaskResource,
)


class CodeTaskTemplate(str, Enum):
    PARALLEL_ANALYZE = "parallel_analyze"
    PARALLEL_REVIEW = "parallel_review"
    ANALYZE_THEN_MODIFY = "analyze_then_modify"


def _node_id(prefix: str, target: str) -> str:
    digest = hashlib.sha1(target.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}_{digest}"


def _read_nodes(targets: Sequence[str], action: str) -> tuple[TaskNode, ...]:
    return tuple(
        TaskNode(
            node_id=_node_id(action, target),
            objective=f"{action} {target}",
            priority=TaskPriority.MEDIUM,
            resources=(TaskResource(target, ResourceMode.READ),),
            capabilities=frozenset({"read", "analyze"}),
            metadata={"action": action, "targets": [target], "template": True},
        )
        for target in targets
    )


def build_code_task_template(
    template: CodeTaskTemplate | str,
    targets: Sequence[str],
    *,
    objective: str = "",
    include_tests: bool = False,
) -> TaskGraph:
    try:
        selected = template if isinstance(template, CodeTaskTemplate) else CodeTaskTemplate(template)
    except ValueError as exc:
        raise ValueError(f"Template de código desconhecido: {template}") from exc
    normalized = tuple(dict.fromkeys(target.strip() for target in targets if target.strip()))
    if not normalized:
        raise ValueError("Template de código exige ao menos um target.")

    if selected == CodeTaskTemplate.PARALLEL_ANALYZE:
        nodes = _read_nodes(normalized, "analyze")
        return TaskGraph(objective or "Análise paralela determinística", nodes)
    if selected == CodeTaskTemplate.PARALLEL_REVIEW:
        nodes = _read_nodes(normalized, "review")
        return TaskGraph(objective or "Review paralelo determinístico", nodes)
    if not objective.strip():
        raise ValueError("analyze_then_modify exige objective.")

    analysis_nodes = _read_nodes(normalized, "analyze")
    change_node = TaskNode(
        node_id="modify_after_analysis",
        objective=objective,
        depends_on=tuple(node.node_id for node in analysis_nodes),
        priority=TaskPriority.HIGH,
        resources=(
            TaskResource("model", ResourceMode.WRITE),
            *(TaskResource(target, ResourceMode.WRITE) for target in normalized),
        ),
        capabilities=frozenset({"read", "write", "process"}),
        metadata={
            "action": "modify",
            "targets": list(normalized),
            "include_tests": include_tests,
            "template": True,
        },
    )
    return TaskGraph(objective, (*analysis_nodes, change_node))
