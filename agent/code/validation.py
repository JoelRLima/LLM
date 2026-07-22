"""Project validation profiles and diagnostic aggregation."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, Sequence

from agent.cancellation import CancellationToken
from agent.code.contracts import Diagnostic, DiagnosticSeverity, ProjectProfile
from agent.code.validation_process import (
    CommandResult,
    CommandSpec,
    ProcessRunner,
    ValidationStatus,
)
from agent.runtime.context import ProcessConcurrencyGate

__all__ = [
    "CommandResult", "CommandSpec", "ProcessRunner", "ProjectValidator",
    "ValidationProfile", "ValidationReport", "ValidationRegistry", "ValidationStatus",
]


@dataclass(frozen=True)
class ValidationProfile:
    commands: tuple[CommandSpec, ...]


@dataclass(frozen=True)
class ValidationReport:
    status: ValidationStatus
    checks: tuple[CommandResult, ...]
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status == ValidationStatus.PASSED


class ValidationProvider(Protocol):
    name: str

    def commands(
        self, project: ProjectProfile, changed_files: Sequence[str], include_tests: bool
    ) -> tuple[CommandSpec, ...]: ...


class PythonValidationProvider:
    name = "python"

    def commands(
        self, project: ProjectProfile, changed_files: Sequence[str], include_tests: bool
    ) -> tuple[CommandSpec, ...]:
        if "python" not in project.languages:
            return ()
        root = Path(project.root)
        python_files = tuple(
            path for path in changed_files
            if Path(path).suffix in {".py", ".pyi"} and (root / path).is_file()
        )
        commands: list[CommandSpec] = []
        if python_files:
            commands.append(CommandSpec("python-syntax", (sys.executable, "-m", "py_compile", *python_files), timeout_seconds=20))
        if include_tests and project.test_roots:
            commands.append(CommandSpec("pytest", (sys.executable, "-m", "pytest", "-q", *project.test_roots), timeout_seconds=120))
        return tuple(commands)


class ValidationRegistry:
    def __init__(self, providers: Sequence[ValidationProvider] = (PythonValidationProvider(),)) -> None:
        self.providers = tuple(providers)

    def build_profile(
        self, project: ProjectProfile, changed_files: Sequence[str], include_tests: bool = False
    ) -> ValidationProfile:
        commands = [
            command for provider in self.providers
            for command in provider.commands(project, changed_files, include_tests)
        ]
        return ValidationProfile(tuple(commands))


class ProjectValidator:
    def __init__(
        self, root: str | Path, cancellation: Optional[CancellationToken] = None,
        registry: Optional[ValidationRegistry] = None,
        process_gate: Optional[ProcessConcurrencyGate] = None,
    ) -> None:
        self.runner = ProcessRunner(Path(root).resolve(), cancellation=cancellation, process_gate=process_gate)
        self.registry = registry or ValidationRegistry()

    def validate(
        self, project: ProjectProfile, changed_files: Sequence[str], *,
        include_tests: bool = False, profile: Optional[ValidationProfile] = None,
    ) -> ValidationReport:
        effective = profile or self.registry.build_profile(project, changed_files, include_tests)
        if not effective.commands:
            return ValidationReport(ValidationStatus.UNAVAILABLE, ())
        results: list[CommandResult] = []
        diagnostics: list[Diagnostic] = []
        for command in effective.commands:
            result = self.runner.run(command)
            results.append(result)
            diagnostic = self._diagnostic(result)
            if diagnostic:
                diagnostics.append(diagnostic)
            if result.status in {ValidationStatus.CANCELLED, ValidationStatus.TIMED_OUT}:
                break
        return ValidationReport(self._overall(results), tuple(results), tuple(diagnostics))

    @staticmethod
    def _diagnostic(result: CommandResult) -> Diagnostic | None:
        if result.status == ValidationStatus.PASSED:
            return None
        severity = DiagnosticSeverity.ERROR if result.status == ValidationStatus.FAILED else DiagnosticSeverity.WARNING
        return Diagnostic(
            code=f"VALIDATION_{result.status.value.upper()}",
            message=(result.stderr or result.stdout or result.status.value)[-2000:],
            severity=severity, file_path=".", source=result.name,
        )

    @staticmethod
    def _overall(results: Sequence[CommandResult]) -> ValidationStatus:
        statuses = {result.status for result in results}
        for status in (ValidationStatus.CANCELLED, ValidationStatus.TIMED_OUT, ValidationStatus.FAILED, ValidationStatus.UNAVAILABLE):
            if status in statuses:
                return status
        return ValidationStatus.PASSED
