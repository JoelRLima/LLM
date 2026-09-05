"""Deterministic impact planning for project validation.

The planner owns selection only.  ``ProjectValidator`` remains the owner of
process execution and result aggregation.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Sequence

from agent.code.contracts import ProjectProfile
from agent.code.intelligence import CodeIntelligenceService
from agent.runtime.path_safety import resolve_workspace_path

from .validation_impact_contracts import (
    TestCoverage,
    ValidationImpactPlan,
    ValidationScope,
    ValidationSelection,
)
from .validation_impact_selection import (
    SUBSYSTEM_TEST_CAP,
    TARGETED_TEST_CAP,
    ValidationImpactSelectionMixin,
)


def _normalize(value: str) -> str:
    normalized = str(value).replace("\\", "/").strip("/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized or "."


def _unique_sorted(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({_normalize(value) for value in values if str(value).strip()}))


class ValidationImpactPlanner(ValidationImpactSelectionMixin):
    """Build one bounded, deterministic validation plan for one invocation."""

    def __init__(
        self,
        root: str | Path,
        intelligence: CodeIntelligenceService | None = None,
        *,
        max_discovery_files: int = 5000,
    ) -> None:
        self.root = Path(root).resolve()
        self.intelligence = intelligence or CodeIntelligenceService(self.root)
        self.max_discovery_files = max(1, max_discovery_files)

    def plan(
        self,
        project: ProjectProfile,
        changed_files: Sequence[str],
        *,
        include_tests: bool = False,
        explicit_test_targets: Sequence[str] = (),
        validation_config: dict[str, object] | None = None,
        full_scope_authorized: bool = False,
    ) -> ValidationImpactPlan:
        changed = _unique_sorted(changed_files)
        config = dict(validation_config or {})
        selections = list(self._static_selections(project, changed, config))
        tests_requested = bool(include_tests)
        if not tests_requested:
            return ValidationImpactPlan(
                changed,
                tuple(selections),
                False,
                TestCoverage.NOT_REQUESTED,
                bool(full_scope_authorized),
            )

        test_files = self._test_files(project.test_roots)
        mapped, reasons = self._mapped_tests(changed, test_files)
        explicit = _unique_sorted(explicit_test_targets)
        for target in explicit:
            if target in test_files or self._safe_python_file(target):
                mapped.add(target)
                reasons.setdefault(target, set()).add("explicit test target")

        if full_scope_authorized:
            full_targets = _unique_sorted(test_files)
            if full_targets:
                selections.append(
                    ValidationSelection(
                        "pytest",
                        ValidationScope.FULL,
                        full_targets,
                        "trusted operator full-scope validation",
                    )
                )
                return ValidationImpactPlan(
                    changed,
                    tuple(selections),
                    True,
                    TestCoverage.FULL_COMPLETE,
                    True,
                )

        mapped_targets = _unique_sorted(mapped)
        if not mapped_targets:
            selections.append(
                ValidationSelection(
                    "pytest",
                    ValidationScope.TARGETED_TESTS,
                    (),
                    "no safe relevant test set could be mapped",
                )
            )
            return ValidationImpactPlan(
                changed,
                tuple(selections),
                True,
                TestCoverage.UNAVAILABLE,
                False,
            )

        if len(mapped_targets) <= TARGETED_TEST_CAP:
            reason = self._targeted_reason(reasons)
            selections.append(
                ValidationSelection(
                    "pytest",
                    ValidationScope.TARGETED_TESTS,
                    mapped_targets,
                    reason,
                )
            )
            return ValidationImpactPlan(
                changed,
                tuple(selections),
                True,
                TestCoverage.TARGETED_COMPLETE,
                False,
            )

        subsystem = self._bounded_subsystem(
            changed,
            mapped_targets,
            test_files,
            project.test_roots,
        )
        if subsystem is not None:
            subsystem_root, subsystem_targets = subsystem
            selections.append(
                ValidationSelection(
                    "pytest",
                    ValidationScope.SUBSYSTEM,
                    subsystem_targets,
                    "targeted mapping exceeded the 20-file cap",
                    expanded_because=(
                        "one deterministic subsystem boundary was derived: "
                        f"{subsystem_root}"
                    ),
                )
            )
            return ValidationImpactPlan(
                changed,
                tuple(selections),
                True,
                TestCoverage.SUBSYSTEM_COMPLETE,
                False,
            )

        selections.append(
            ValidationSelection(
                "pytest",
                ValidationScope.TARGETED_TESTS,
                (),
                "targeted mapping exceeded the 20-file cap and no bounded subsystem was derived",
            )
        )
        return ValidationImpactPlan(
            changed,
            tuple(selections),
            True,
            TestCoverage.UNAVAILABLE,
            False,
        )

    def fingerprint(
        self,
        plan: ValidationImpactPlan,
        *,
        validation_config: dict[str, object] | None = None,
    ) -> str:
        file_identity: list[dict[str, str]] = []
        for relative in plan.changed_files:
            try:
                path = resolve_workspace_path(self.root, relative, require_file=True)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except (OSError, ValueError):
                digest = "missing"
            file_identity.append({"path": relative, "sha256": digest})
        selected_config: dict[str, object] = {}
        if validation_config is not None:
            for key in sorted(validation_config):
                if key in {"enabled", "ruff", "mypy", "pytest"}:
                    selected_config[key] = validation_config[key]
        material = {
            "changed_files": file_identity,
            "selections": [item.to_dict() for item in plan.selections],
            "validation_config": selected_config,
            "full_scope_authorized": plan.full_scope_authorized,
        }
        encoded = json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

__all__ = [
    "SUBSYSTEM_TEST_CAP",
    "TARGETED_TEST_CAP",
    "TestCoverage",
    "ValidationImpactPlan",
    "ValidationImpactPlanner",
    "ValidationScope",
    "ValidationSelection",
]
