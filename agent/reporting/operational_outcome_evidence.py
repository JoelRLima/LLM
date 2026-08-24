"""Canonical invocation and incident evidence for operational outcomes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agent.execution_incidents import (
    CANONICAL_COMMIT_FAILED,
    EFFECT_PROVEN,
    EFFECT_UNKNOWN,
)
from agent.planning.failure_policy import FailureClass, classify_failure
from agent.reporting.observation_evidence import project_artifact_evidence


@dataclass
class OperationalEvidence:
    files: set[str] = field(default_factory=set)
    invocation_ids: list[str] = field(default_factory=list)
    failed_invocations: list[str] = field(default_factory=list)
    failed_statuses: list[str] = field(default_factory=list)
    recovered_invocations: list[str] = field(default_factory=list)
    unrecovered_invocations: list[str] = field(default_factory=list)
    unrecovered_hard_invocations: list[str] = field(default_factory=list)
    physical_effect_unknown: bool = False
    validation_status: str | None = None
    rollback_occurred: bool = False
    mutation_occurred: bool = False
    incident_present: bool = False


def canonical_execution_incidents(state: Any) -> tuple[Mapping[str, Any], ...]:
    incidents = getattr(state, "execution_incidents", ()) or ()
    return tuple(
        item
        for item in incidents
        if isinstance(item, Mapping)
        and item.get("incident_type") == CANONICAL_COMMIT_FAILED
    )


def has_canonical_commit_incident(state: Any) -> bool:
    return bool(canonical_execution_incidents(state))


def collect_operational_evidence(state: Any) -> OperationalEvidence:
    facts = OperationalEvidence()
    for history_index, entry in enumerate(getattr(state, "tool_history", ()) or (), start=1):
        if isinstance(entry, dict):
            _add_history_entry(facts, state, history_index, entry)
    for incident in canonical_execution_incidents(state):
        _add_incident(facts, incident)
    return facts


def _add_history_entry(
    facts: OperationalEvidence,
    state: Any,
    history_index: int,
    entry: dict[str, Any],
) -> None:
    result = entry.get("result")
    if not isinstance(result, dict):
        return
    facts.physical_effect_unknown = (
        facts.physical_effect_unknown or _physical_effect_unknown(result)
    )
    invocation_id = entry.get("invocation_id") or result.get("invocation_id")
    if invocation_id is not None:
        facts.invocation_ids.append(str(invocation_id))
    failure_class = classify_failure(result)
    if failure_class in {FailureClass.LOCAL, FailureClass.HARD}:
        _add_failure(facts, state, history_index, entry, result, invocation_id, failure_class)
    artifact = project_artifact_evidence(result)
    facts.files.update(artifact.mutated_files)
    facts.validation_status = artifact.validation_status or facts.validation_status
    facts.rollback_occurred = facts.rollback_occurred or artifact.rollback_occurred
    facts.mutation_occurred = facts.mutation_occurred or artifact.mutation_occurred


def _physical_effect_unknown(result: Mapping[str, Any]) -> bool:
    error_detail = result.get("error_detail")
    data = result.get("data")
    return bool(
        isinstance(error_detail, Mapping)
        and error_detail.get("physical_effect_unknown") is True
        or isinstance(data, Mapping)
        and data.get("physical_effect_unknown") is True
    )


def _add_failure(
    facts: OperationalEvidence,
    state: Any,
    history_index: int,
    entry: dict[str, Any],
    result: Mapping[str, Any],
    invocation_id: Any,
    failure_class: FailureClass,
) -> None:
    reference = str(invocation_id) if invocation_id is not None else f"history:{history_index}"
    facts.failed_invocations.append(reference)
    facts.failed_statuses.append(str(result.get("status") or "failed"))
    later_recovery = getattr(state, "_later_recovery", None)
    if callable(later_recovery) and later_recovery(history_index - 1, entry):
        facts.recovered_invocations.append(reference)
        return
    facts.unrecovered_invocations.append(reference)
    if failure_class is FailureClass.HARD:
        facts.unrecovered_hard_invocations.append(reference)


def _add_incident(facts: OperationalEvidence, incident: Mapping[str, Any]) -> None:
    facts.incident_present = True
    invocation_id = incident.get("invocation_id")
    if invocation_id is not None:
        reference = str(invocation_id)
        facts.invocation_ids.append(reference)
        facts.failed_invocations.append(reference)
    facts.failed_statuses.append("unverified")
    effect_state = incident.get("effect_state")
    if effect_state == EFFECT_PROVEN:
        facts.mutation_occurred = True
        raw_files = incident.get("affected_files")
        if isinstance(raw_files, (list, tuple)):
            facts.files.update(str(path) for path in raw_files)
    elif effect_state == EFFECT_UNKNOWN:
        facts.physical_effect_unknown = True
    omitted_states = incident.get("omitted_effect_states")
    if isinstance(omitted_states, (list, tuple)):
        if EFFECT_PROVEN in omitted_states:
            facts.mutation_occurred = True
        if EFFECT_UNKNOWN in omitted_states:
            facts.physical_effect_unknown = True
    if incident.get("rollback_occurred") is True:
        facts.rollback_occurred = True


__all__ = [
    "OperationalEvidence",
    "collect_operational_evidence",
    "has_canonical_commit_incident",
]
