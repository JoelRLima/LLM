"""Execution and aggregation mechanics for project validation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Optional

from agent.code.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    ProjectProfile,
)
from agent.code.discovery import IGNORED_DIRECTORIES
from agent.code.validation_impact import (
    TestCoverage,
    ValidationImpactPlan,
    ValidationScope,
)
from agent.code.validation_models import ValidationProfile, ValidationRegistry, ValidationReport
from agent.code.validation_preflight import find_external_link, link_status
from agent.code.validation_process import (
    CommandResult,
    ProcessRunner,
    ValidationStatus,
)
from agent.runtime.path_safety import resolve_workspace_path, workspace_relative_path


class ValidationRunnerMixin:
    """Private ProjectValidator methods that execute and aggregate checks."""

    root: Path
    registry: ValidationRegistry
    runner: ProcessRunner

    @staticmethod
    def _invalid_path_report(message: str) -> ValidationReport:
        diagnostic = Diagnostic(
            code="VALIDATION_PATH_ESCAPE",
            message=message,
            severity=DiagnosticSeverity.SECURITY,
            file_path=".",
            source="validation-preflight",
        )
        return ValidationReport(ValidationStatus.FAILED, (), (diagnostic,))

    def _normalize_paths(self, values: Sequence[str]) -> tuple[str, ...]:
        return tuple(workspace_relative_path(self.root, value) for value in values)

    def _prepare_inputs(
        self,
        project: ProjectProfile,
        changed_files: Sequence[str],
        include_tests: bool,
        explicit_test_targets: Sequence[str] = (),
    ) -> tuple[ProjectProfile, tuple[str, ...]]:
        project_root = Path(project.root).resolve()
        if project_root != self.root:
            raise ValueError(f"Project profile is outside workspace: {project.root}")
        manifests = self._normalize_paths(project.manifests)
        source_roots = self._normalize_paths(project.source_roots)
        test_roots = self._normalize_paths(project.test_roots)
        if include_tests:
            for test_root in test_roots:
                resolve_workspace_path(
                    self.root,
                    test_root,
                    require_directory=True,
                )
        for test_target in explicit_test_targets:
            resolve_workspace_path(self.root, test_target, require_file=True)
        normalized_project = replace(
            project,
            root=str(self.root),
            manifests=manifests,
            source_roots=source_roots,
            test_roots=test_roots,
        )
        return normalized_project, self._normalize_paths(changed_files)

    def _find_external_link(
        self,
        start: Path | None = None,
        *,
        ignored_directories: frozenset[str] = IGNORED_DIRECTORIES,
        reject_all_links: bool = False,
    ) -> str | None:
        return find_external_link(
            self.root,
            start,
            ignored_directories=ignored_directories,
            reject_all_links=reject_all_links,
        )

    def _link_status(
        self,
        candidate: Path,
        *,
        reject_all_links: bool,
    ) -> tuple[bool, bool]:
        return link_status(self.root, candidate, reject_all_links=reject_all_links)

    def _test_link_failure(
        self,
        project: ProjectProfile,
        include_tests: bool,
        plan: ValidationImpactPlan | None = None,
    ) -> ValidationReport | None:
        if not include_tests:
            return None
        roots = tuple(project.test_roots)
        if plan is not None and plan.test_targets:
            roots = tuple(
                sorted(
                    {
                        root
                        for root in project.test_roots
                        if any(
                            item == root or item.startswith(root + "/")
                            for item in plan.test_targets
                        )
                    }
                )
            )
        external_link = next(
            (
                unsafe
                for test_root in roots
                if (
                    unsafe := self._find_external_link(
                        resolve_workspace_path(
                            self.root,
                            test_root,
                            require_directory=True,
                        ),
                        ignored_directories=frozenset(),
                        reject_all_links=True,
                    )
                )
                is not None
            ),
            None,
        )
        external_link = external_link or self._find_external_link()
        if external_link is None:
            return None
        return self._invalid_path_report(
            "Test validation refused: symlink or junction cannot be confined "
            f"inside workspace: {external_link}"
        )

    def _effective_profile(
        self,
        project: ProjectProfile,
        changed_files: Sequence[str],
        include_tests: bool,
        profile: Optional[ValidationProfile],
        plan: ValidationImpactPlan,
        *,
        model_actionable: bool,
    ) -> ValidationProfile:
        minimum = self.registry.build_profile(
            project,
            changed_files,
            include_tests,
            selected_test_files=plan.test_targets,
            model_actionable=model_actionable,
        )
        if profile is None:
            return minimum
        commands = list(profile.commands)
        commands.extend(command for command in minimum.commands if command not in commands)
        return ValidationProfile(tuple(commands))

    def _run_profile(
        self,
        profile: ValidationProfile,
        plan: ValidationImpactPlan,
        plan_fingerprint: str,
    ) -> ValidationReport:
        if not profile.commands:
            return ValidationReport(
                ValidationStatus.UNAVAILABLE,
                (),
                plan=plan,
                test_coverage=(
                    TestCoverage.UNAVAILABLE
                    if plan.tests_requested
                    else TestCoverage.NOT_REQUESTED
                ),
                plan_fingerprint=plan_fingerprint,
            )
        results: list[CommandResult] = []
        diagnostics: list[Diagnostic] = []
        for command in profile.commands:
            result = self.runner.run(command)
            results.append(result)
            if diagnostic := self._diagnostic(result):
                diagnostics.append(diagnostic)
            if result.status in {ValidationStatus.CANCELLED, ValidationStatus.TIMED_OUT}:
                break
        coverage = self._test_coverage(plan, results)
        return ValidationReport(
            self._overall(results),
            tuple(results),
            tuple(diagnostics),
            plan,
            coverage,
            plan_fingerprint,
        )

    @staticmethod
    def _test_coverage(
        plan: ValidationImpactPlan,
        results: Sequence[CommandResult],
    ) -> TestCoverage:
        if not plan.tests_requested:
            return TestCoverage.NOT_REQUESTED
        pytest_result = next(
            (result for result in results if result.name == "pytest"),
            None,
        )
        if pytest_result is None or pytest_result.status in {
            ValidationStatus.UNAVAILABLE,
            ValidationStatus.CANCELLED,
            ValidationStatus.TIMED_OUT,
        }:
            return TestCoverage.UNAVAILABLE
        selection = plan.pytest_selection
        if selection is None:
            return TestCoverage.UNAVAILABLE
        if selection.scope is ValidationScope.FULL:
            return TestCoverage.FULL_COMPLETE
        if selection.scope is ValidationScope.SUBSYSTEM:
            return TestCoverage.SUBSYSTEM_COMPLETE
        return TestCoverage.TARGETED_COMPLETE

    @staticmethod
    def _diagnostic(result: CommandResult) -> Diagnostic | None:
        if result.status == ValidationStatus.PASSED:
            return None
        severity = (
            DiagnosticSeverity.ERROR
            if result.status == ValidationStatus.FAILED
            else DiagnosticSeverity.WARNING
        )
        return Diagnostic(
            code=f"VALIDATION_{result.status.value.upper()}",
            message=(result.stderr or result.stdout or result.status.value)[-2000:],
            severity=severity,
            file_path=".",
            source=result.name,
        )

    @staticmethod
    def _overall(results: Sequence[CommandResult]) -> ValidationStatus:
        statuses = {result.status for result in results}
        for status in (
            ValidationStatus.CANCELLED,
            ValidationStatus.TIMED_OUT,
            ValidationStatus.FAILED,
            ValidationStatus.UNAVAILABLE,
        ):
            if status in statuses:
                return status
        return ValidationStatus.PASSED


__all__ = ["ValidationRunnerMixin"]
