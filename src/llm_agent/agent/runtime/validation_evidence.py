"""Current validation facts shared by completion and read-only reporting."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from llm_agent.agent.runtime.mutation_evidence import MutationEvidence, artifact_metadata, project_mutation_evidence
from llm_agent.agent.runtime.outcome_taxonomy import OperationalStatus


@dataclass(frozen=True, slots=True)
class ResourceValidation:
    resource: str
    validated: bool
    observation_index: int
    validation_status: str | None


@dataclass(frozen=True, slots=True)
class CurrentValidationEvidence:
    resources: tuple[ResourceValidation, ...]
    unknown_footprint: bool
    ran: bool
    last_validation_status: str | None

    @property
    def satisfied(self) -> bool:
        return not self.unknown_footprint and all(item.validated for item in self.resources)

    def summary(self) -> dict[str, Any]:
        outcome = self.last_validation_status
        if self.resources or self.unknown_footprint:
            outcome = "passed" if self.satisfied else "unverified"
            if any(not item.validated and item.validation_status == "failed" for item in self.resources):
                outcome = "failed"
        return {"ran": self.ran, "outcome": outcome}


def _mutation_records(result: Any) -> tuple[MutationEvidence, ...]:
    records = tuple(project_mutation_evidence({"metadata": item}) for item in artifact_metadata(result))
    if any(item.attempted or item.occurred or item.survives for item in records):
        return records
    return (project_mutation_evidence(result),)


def _result_explicitly_succeeded(result: Any) -> bool:
    """Check typed result attributes; legacy mappings are not runtime authority."""
    ok, status = getattr(result, "ok", None), getattr(result, "status", None)
    return bool(
        ok is True
        and getattr(status, "value", status) == OperationalStatus.SUCCEEDED.value
    )


def _validated(evidence: MutationEvidence, result: Any) -> bool:
    return (
        evidence.survives
        and not evidence.rollback_occurred
        and evidence.final_state != "unknown"
        and isinstance(evidence.validation_status, str)
        and evidence.validation_status.strip().casefold() == "passed"
        and _result_explicitly_succeeded(result)
    )


def _resource_records(result: Any, index: int) -> tuple[dict[str, ResourceValidation], bool]:
    current: dict[str, ResourceValidation] = {}
    unknown = False
    for evidence in _mutation_records(result):
        if not (evidence.occurred or evidence.survives or evidence.rollback_occurred):
            continue
        resources = (*evidence.affected_resources, *evidence.mutated_resources, *evidence.surviving_resources)
        unknown = unknown or not resources
        for resource in resources:
            previous = current.get(resource)
            validated = _validated(evidence, result) and (previous is None or previous.validated)
            status = evidence.validation_status
            if previous is not None and previous.validation_status == "failed":
                status = "failed"
            current[resource] = ResourceValidation(resource, validated, index, status)
    return current, unknown


def current_validation_evidence(results: Iterable[Any]) -> CurrentValidationEvidence:
    """Later mutations replace earlier facts only for the same concrete resources.

    Conflicting artifacts within one invocation cannot validate each other.
    A missing footprint cannot be superseded by a later unrelated mutation.
    Empty evidence leaves the existing pending-effect owner responsible.
    """
    resources: dict[str, ResourceValidation] = {}
    unknown = False
    last_status: str | None = None
    for index, result in enumerate(results):
        for record in _mutation_records(result):
            if record.validation_status is not None:
                last_status = record.validation_status
        current, missing_footprint = _resource_records(result, index)
        resources.update(current)
        unknown = unknown or missing_footprint
    return CurrentValidationEvidence(tuple(resources.values()), unknown, last_status is not None, last_status)


__all__ = ["CurrentValidationEvidence", "ResourceValidation", "current_validation_evidence"]
