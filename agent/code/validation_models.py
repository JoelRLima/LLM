"""Validation profile, report, and provider registry contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from agent.code.contracts import Diagnostic, ProjectProfile
from agent.code.validation_external import MypyValidationProvider, RuffValidationProvider
from agent.code.validation_impact import (
    TestCoverage,
    ValidationImpactPlan,
    ValidationImpactPlanner,
    ValidationScope,
    ValidationSelection,
)
from agent.code.validation_process import CommandResult, CommandSpec, ValidationStatus
from agent.code.validation_python import PythonValidationProvider


@dataclass(frozen=True)
class ValidationProfile:
    commands: tuple[CommandSpec, ...]


@dataclass(frozen=True)
class ValidationReport:
    status: ValidationStatus
    checks: tuple[CommandResult, ...]
    diagnostics: tuple[Diagnostic, ...] = ()
    plan: ValidationImpactPlan | None = None
    test_coverage: TestCoverage = TestCoverage.NOT_REQUESTED
    plan_fingerprint: str | None = None

    @property
    def execution_status(self) -> ValidationStatus:
        return self.status

    @property
    def effective_status(self) -> ValidationStatus:
        if self.status != ValidationStatus.PASSED:
            return self.status
        if (
            self.plan is not None
            and self.plan.tests_requested
            and self.test_coverage is TestCoverage.UNAVAILABLE
        ):
            return ValidationStatus.UNAVAILABLE
        return ValidationStatus.PASSED

    @property
    def passed(self) -> bool:
        return self.effective_status == ValidationStatus.PASSED

    @property
    def metadata(self) -> dict[str, object]:
        selections: list[dict[str, object]] = []
        if self.plan is not None:
            status_by_command = {
                result.name: result.status.value for result in self.checks
            }
            for selection in self.plan.selections:
                record = selection.to_dict()
                record["status"] = status_by_command.get(selection.command_kind)
                selections.append(record)
        return {
            "execution_status": self.execution_status.value,
            "effective_status": self.effective_status.value,
            "tests_requested": bool(self.plan and self.plan.tests_requested),
            "test_coverage": self.test_coverage.value,
            "plan_fingerprint": self.plan_fingerprint,
            "selections": selections,
        }


class ValidationProvider(Protocol):
    name: str

    def commands(
        self,
        project: ProjectProfile,
        changed_files: Sequence[str],
        include_tests: bool,
        selected_test_files: Sequence[str] = (),
    ) -> tuple[CommandSpec, ...]: ...


class ValidationRegistry:
    def __init__(
        self,
        providers: Sequence[ValidationProvider] | None = None,
        *,
        validation_config: Mapping[str, object] | None = None,
    ) -> None:
        self.validation_config = dict(validation_config or {})
        if providers is not None:
            self.providers = tuple(providers)
            return
        configured: list[ValidationProvider] = [PythonValidationProvider()]
        if self.validation_config.get("enabled", True):
            if self.validation_config.get("ruff") is True:
                configured.append(RuffValidationProvider())
            if self.validation_config.get("mypy") is True:
                configured.append(MypyValidationProvider())
        self.providers = tuple(configured)

    def build_profile(
        self,
        project: ProjectProfile,
        changed_files: Sequence[str],
        include_tests: bool = False,
        *,
        selected_test_files: Sequence[str] = (),
        model_actionable: bool = False,
    ) -> ValidationProfile:
        configured_tests = (
            self.validation_config.get("enabled", True)
            and self.validation_config.get("pytest") is True
        )
        tests_requested = bool(
            include_tests or (configured_tests and not model_actionable)
        )
        commands: list[CommandSpec] = []
        for provider in self.providers:
            try:
                provider_commands = provider.commands(
                    project,
                    changed_files,
                    tests_requested,
                    selected_test_files,
                )
            except TypeError:
                provider_commands = provider.commands(
                    project,
                    changed_files,
                    tests_requested,
                )
            commands.extend(provider_commands)
        return ValidationProfile(tuple(commands))


__all__ = [
    "ValidationProfile",
    "ValidationProvider",
    "ValidationRegistry",
    "ValidationReport",
    "MypyValidationProvider",
    "RuffValidationProvider",
    "TestCoverage",
    "ValidationImpactPlan",
    "ValidationImpactPlanner",
    "ValidationScope",
    "ValidationSelection",
]
