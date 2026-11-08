"""Immutable values for requirement-to-plan coverage projections."""

from __future__ import annotations

from dataclasses import dataclass

MAX_COVERAGE_ITEMS = 64
MAX_REQUIREMENT_TEXT = 500


class PlanCoverageError(ValueError):
    """A declared plan link is unknown or belongs to a stale definition."""


@dataclass(frozen=True, slots=True)
class StepCoverage:
    """Bounded operational and canonical evidence references for one step."""

    step_id: str
    step_status: str
    invocation_refs: tuple[str, ...]
    evidence_refs: tuple[int | str, ...]
    canonical_evidence_refs: tuple[int | str, ...]
    obligation_ids: tuple[str, ...]
    canonical_statuses: tuple[tuple[str, str], ...]
    total_invocation_refs: int
    omitted_invocation_refs: int
    total_evidence_refs: int
    omitted_evidence_refs: int
    total_canonical_evidence_refs: int
    omitted_canonical_evidence_refs: int


@dataclass(frozen=True, slots=True)
class RequirementCoverage:
    """One textual criterion; linked steps never imply verified completion."""

    requirement_id: str
    source: str
    field: str
    index: int
    path: str
    phase_id: str | None
    text: str
    status: str
    verification_status: str
    text_truncated: bool
    steps: tuple[StepCoverage, ...]
    step_count: int
    omitted_steps: int


@dataclass(frozen=True, slots=True)
class PlanCoverageProjection:
    """Bounded public rows with full calculated totals and explicit omissions."""

    requirements: tuple[RequirementCoverage, ...]
    unresolved_requirement_ids: tuple[str, ...]
    unmapped_requirement_ids: tuple[str, ...]
    total_requirements: int
    omitted_requirements: int
    total_unresolved: int
    omitted_unresolved: int
    total_unmapped: int
    omitted_unmapped: int

    @property
    def truncated(self) -> bool:
        return (
            self.omitted_requirements > 0
            or self.omitted_unresolved > 0
            or self.omitted_unmapped > 0
            or any(item.omitted_steps or item.text_truncated for item in self.requirements)
            or any(
                step.omitted_invocation_refs
                or step.omitted_evidence_refs
                or step.omitted_canonical_evidence_refs
                for item in self.requirements
                for step in item.steps
            )
        )


@dataclass(frozen=True, slots=True)
class RequirementCatalogEntry:
    requirement_id: str
    field: str
    path: str
    text: str


@dataclass(frozen=True, slots=True)
class RequirementCatalog:
    entries: tuple[RequirementCatalogEntry, ...]
    total: int
    omitted: int


__all__ = [
    "PlanCoverageError",
    "PlanCoverageProjection",
    "MAX_COVERAGE_ITEMS",
    "MAX_REQUIREMENT_TEXT",
    "RequirementCatalog",
    "RequirementCatalogEntry",
    "RequirementCoverage",
    "StepCoverage",
]
