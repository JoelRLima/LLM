"""Project validation profiles and diagnostic aggregation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Optional

from agent.cancellation import CancellationToken
from agent.code.contracts import ProjectProfile
from agent.code.validation_models import (
    MypyValidationProvider,
    RuffValidationProvider,
    TestCoverage,
    ValidationImpactPlan,
    ValidationImpactPlanner,
    ValidationProfile,
    ValidationRegistry,
    ValidationReport,
    ValidationScope,
    ValidationSelection,
)
from agent.code.validation_process import (
    CommandResult,
    CommandSpec,
    ProcessRunner,
    ValidationStatus,
)
from agent.code.validation_runner import ValidationRunnerMixin
from agent.runtime.context import ProcessConcurrencyGate
from agent.runtime.path_safety import workspace_relative_path

__all__ = [
    "CommandResult", "CommandSpec", "ProcessRunner", "ProjectValidator",
    "ValidationProfile", "ValidationReport", "ValidationRegistry", "ValidationStatus",
    "TestCoverage", "ValidationImpactPlan", "ValidationImpactPlanner",
    "ValidationScope", "ValidationSelection",
    "MypyValidationProvider", "RuffValidationProvider",
]

class ProjectValidator(ValidationRunnerMixin):
    def __init__(
        self, root: str | Path, cancellation: Optional[CancellationToken] = None,
        registry: Optional[ValidationRegistry] = None,
        process_gate: Optional[ProcessConcurrencyGate] = None,
        validation_config: Mapping[str, object] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.runner = ProcessRunner(
            self.root,
            cancellation=cancellation,
            process_gate=process_gate,
        )
        self.registry = registry or ValidationRegistry(
            validation_config=validation_config,
        )
        self.planner = ValidationImpactPlanner(self.root)

    def validate(
        self, project: ProjectProfile, changed_files: Sequence[str], *,
        include_tests: bool = False, profile: Optional[ValidationProfile] = None,
        explicit_test_targets: Sequence[str] = (),
        full_scope_authorized: bool = False,
        model_actionable: bool = False,
    ) -> ValidationReport:
        configured_tests = (
            self.registry.validation_config.get("enabled", True)
            and self.registry.validation_config.get("pytest") is True
        )
        effective_include_tests = bool(
            include_tests or (configured_tests and not model_actionable)
        )
        try:
            safe_project, safe_changed_files = self._prepare_inputs(
                project,
                changed_files,
                effective_include_tests,
                explicit_test_targets,
            )
        except (OSError, ValueError) as exc:
            return self._invalid_path_report(str(exc))
        safe_test_targets = tuple(
            workspace_relative_path(self.root, value)
            for value in explicit_test_targets
        )
        plan = self.planner.plan(
            safe_project,
            safe_changed_files,
            include_tests=effective_include_tests,
            explicit_test_targets=safe_test_targets,
            validation_config=dict(self.registry.validation_config),
            full_scope_authorized=full_scope_authorized and not model_actionable,
        )
        plan_fingerprint = self.planner.fingerprint(
            plan,
            validation_config=dict(self.registry.validation_config),
        )
        link_failure = self._test_link_failure(
            safe_project,
            effective_include_tests,
            plan,
        )
        if link_failure is not None:
            return link_failure
        effective = self._effective_profile(
            safe_project,
            safe_changed_files,
            effective_include_tests,
            profile,
            plan,
            model_actionable=model_actionable,
        )
        return self._run_profile(effective, plan, plan_fingerprint)
