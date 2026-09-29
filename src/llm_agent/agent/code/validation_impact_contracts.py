"""Contracts for deterministic validation-impact planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ValidationScope(str, Enum):
    FILE = "file"
    TARGETED_TESTS = "targeted_tests"
    SUBSYSTEM = "subsystem"
    FULL = "full"


class TestCoverage(str, Enum):
    NOT_REQUESTED = "not_requested"
    TARGETED_COMPLETE = "targeted_complete"
    SUBSYSTEM_COMPLETE = "subsystem_complete"
    FULL_COMPLETE = "full_complete"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ValidationSelection:
    command_kind: str
    scope: ValidationScope
    targets: tuple[str, ...]
    reason: str
    expanded_because: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "command_kind": self.command_kind,
            "scope": self.scope.value,
            "targets": list(self.targets),
            "count": len(self.targets),
            "reason": self.reason,
            "expanded_because": self.expanded_because,
        }


@dataclass(frozen=True)
class ValidationImpactPlan:
    changed_files: tuple[str, ...]
    selections: tuple[ValidationSelection, ...]
    tests_requested: bool
    test_coverage: TestCoverage
    full_scope_authorized: bool

    @property
    def pytest_selection(self) -> ValidationSelection | None:
        return next(
            (item for item in self.selections if item.command_kind == "pytest"),
            None,
        )

    @property
    def test_targets(self) -> tuple[str, ...]:
        selection = self.pytest_selection
        return selection.targets if selection is not None else ()

    def to_dict(self) -> dict[str, object]:
        return {
            "changed_files": list(self.changed_files),
            "selections": [item.to_dict() for item in self.selections],
            "tests_requested": self.tests_requested,
            "test_coverage": self.test_coverage.value,
            "full_scope_authorized": self.full_scope_authorized,
        }


__all__ = [
    "TestCoverage",
    "ValidationImpactPlan",
    "ValidationScope",
    "ValidationSelection",
]
