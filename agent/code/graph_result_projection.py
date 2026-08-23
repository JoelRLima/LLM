"""Canonical projection of multitask graph results into a task result."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from agent.reporting.observation_evidence import project_artifact_evidence
from agent.runtime.context import TaskResult, TaskStatus


def _graph_status(graph_result: Any) -> TaskStatus:
    states = {state.value for state in graph_result.states.values()}
    if graph_result.succeeded:
        return TaskStatus.SUCCEEDED
    if "failed" in states:
        return TaskStatus.FAILED
    if "blocked" in states:
        return TaskStatus.BLOCKED
    if "unverified" in states:
        return TaskStatus.UNVERIFIED
    if "cancelled" in states:
        return TaskStatus.CANCELLED
    return TaskStatus.FAILED


def _empty_node_metadata(graph_result: Any, node_id: str) -> dict[str, Any]:
    return {
        "status": graph_result.states[node_id].value,
        "summary": None,
        "error": graph_result.errors.get(node_id),
        "affected_files": [],
        "attempted_files": [],
        "mutation_occurred": False,
        "persisted_mutation": False,
        "rollback_occurred": False,
        "validation": None,
        "invocation_ids": [],
    }


def _project_node(result: Any) -> tuple[dict[str, Any], Any]:
    evidence = project_artifact_evidence({"data": asdict(result)})
    invocation_ids: list[str] = []
    for artifact in result.artifacts:
        for key in ("validation_invocation_id", "invocation_id"):
            value = artifact.metadata.get(key)
            if value is not None:
                invocation_ids.append(str(value))
    metadata = {
        "status": result.status.value,
        "summary": result.summary,
        "error": result.error,
        "affected_files": list(evidence.surviving_files),
        "attempted_files": list(evidence.affected_files),
        "mutation_occurred": evidence.mutation_occurred,
        "persisted_mutation": evidence.persisted_mutation,
        "rollback_occurred": evidence.rollback_occurred,
        "validation": evidence.validation_status,
        "invocation_ids": list(dict.fromkeys(invocation_ids)),
    }
    return metadata, evidence


def project_graph_result(graph_result: Any) -> TaskResult:
    status = _graph_status(graph_result)
    node_metadata: dict[str, Any] = {}
    affected_files: set[str] = set()
    attempted_files: set[str] = set()
    persisted_mutation = False
    mutation_occurred = False
    rollback_occurred = False
    validation_statuses: dict[str, str] = {}
    for node_id in graph_result.states:
        result = graph_result.results.get(node_id)
        if result is None:
            node_metadata[node_id] = _empty_node_metadata(graph_result, node_id)
            continue
        metadata, evidence = _project_node(result)
        affected_files.update(evidence.surviving_files)
        attempted_files.update(evidence.affected_files)
        persisted_mutation = persisted_mutation or evidence.persisted_mutation
        mutation_occurred = mutation_occurred or evidence.mutation_occurred
        rollback_occurred = rollback_occurred or evidence.rollback_occurred
        if evidence.validation_status is not None:
            validation_statuses[node_id] = evidence.validation_status
        node_metadata[node_id] = metadata
    metadata = {
        "states": {key: value.value for key, value in graph_result.states.items()},
        "execution_order": graph_result.execution_order,
        "errors": graph_result.errors,
        "nodes": node_metadata,
        "affected_files": tuple(sorted(affected_files)),
        "attempted_files": tuple(sorted(attempted_files)),
        "mutation_occurred": mutation_occurred,
        "persisted_mutation": persisted_mutation,
        "surviving_mutation": persisted_mutation,
        "rollback_occurred": rollback_occurred,
        "validation_statuses": validation_statuses,
    }
    succeeded = sum(state.value == "succeeded" for state in graph_result.states.values())
    return TaskResult(
        status,
        summary=f"TaskGraph concluído: {succeeded}/{len(graph_result.states)} nós com sucesso.",
        metadata=metadata,
    )


__all__ = ["project_graph_result"]
