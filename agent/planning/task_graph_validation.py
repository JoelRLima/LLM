from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class GraphValidationReport:
    valid: bool
    errors: tuple[str, ...] = ()


class TaskGraphValidator:
    def validate(self, graph: Any) -> GraphValidationReport:
        errors = self._graph_errors(graph)
        known = set(graph.by_id())
        for node in graph.nodes:
            errors.extend(self._node_errors(node, known))
        if not errors and self._has_cycle(graph.nodes):
            errors.append("TaskGraph contém ciclo de dependências.")
        return GraphValidationReport(not errors, tuple(errors))

    @staticmethod
    def _graph_errors(graph: Any) -> list[str]:
        ids = [node.node_id for node in graph.nodes]
        errors = [] if graph.nodes else ["TaskGraph vazio."]
        if any(not node_id.strip() for node_id in ids):
            errors.append("Todo nó deve possuir id não vazio.")
        duplicates = sorted({node_id for node_id in ids if ids.count(node_id) > 1})
        if duplicates:
            errors.append(f"IDs duplicados: {', '.join(duplicates)}.")
        return errors

    @staticmethod
    def _node_errors(node: Any, known: set[str]) -> list[str]:
        errors: list[str] = []
        if not isinstance(node.objective, str) or not node.objective.strip():
            errors.append(f"Nó '{node.node_id}' deve possuir objetivo não vazio.")
        missing = sorted(set(node.depends_on) - known)
        if missing:
            errors.append(f"Nó '{node.node_id}' depende de ids ausentes: {', '.join(missing)}.")
        if node.node_id in node.depends_on:
            errors.append(f"Nó '{node.node_id}' depende de si mesmo.")
        if any(not resource.name.strip() for resource in node.resources):
            errors.append(f"Nó '{node.node_id}' contém recurso sem nome.")
        if any(not capability.strip() for capability in node.capabilities):
            errors.append(f"Nó '{node.node_id}' contém capacidade sem nome.")
        return errors

    @staticmethod
    def _has_cycle(nodes: Iterable[Any]) -> bool:
        dependencies = {node.node_id: set(node.depends_on) for node in nodes}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> bool:
            if node_id in visiting:
                return True
            if node_id in visited:
                return False
            visiting.add(node_id)
            cyclic = any(visit(dependency) for dependency in dependencies[node_id])
            visiting.remove(node_id)
            visited.add(node_id)
            return cyclic

        return any(visit(node_id) for node_id in dependencies)
