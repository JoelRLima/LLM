"""Project validation profiles and diagnostic aggregation."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional, Protocol, Sequence

from agent.cancellation import CancellationToken
from agent.code.contracts import Diagnostic, DiagnosticSeverity, ProjectProfile
from agent.code.discovery import IGNORED_DIRECTORIES
from agent.code.path_safety import (
    is_link_like,
    resolve_workspace_path,
    workspace_relative_path,
)
from agent.code.validation_process import (
    CommandResult,
    CommandSpec,
    ProcessRunner,
    ValidationStatus,
)
from agent.code.validation_python import PythonValidationProvider
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
        self.root = Path(root).resolve()
        self.runner = ProcessRunner(
            self.root,
            cancellation=cancellation,
            process_gate=process_gate,
        )
        self.registry = registry or ValidationRegistry()

    @staticmethod
    def _invalid_path_report(message: str) -> ValidationReport:
        diagnostic = Diagnostic(
            code="VALIDATION_PATH_ESCAPE",
            message=message,
            severity=DiagnosticSeverity.SECURITY,
            file_path=".",
            source="validation-preflight",
        )
        return ValidationReport(
            ValidationStatus.FAILED,
            (),
            (diagnostic,),
        )

    def _normalize_paths(
        self,
        values: Sequence[str],
    ) -> tuple[str, ...]:
        return tuple(
            workspace_relative_path(self.root, value)
            for value in values
        )

    def _prepare_inputs(
        self,
        project: ProjectProfile,
        changed_files: Sequence[str],
        include_tests: bool,
    ) -> tuple[ProjectProfile, tuple[str, ...]]:
        project_root = Path(project.root).resolve()
        if project_root != self.root:
            raise ValueError(
                f"Perfil de projeto fora do workspace: {project.root}"
            )
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
        for current, directory_names, file_names in os.walk(
            start or self.root,
            topdown=True,
            followlinks=False,
        ):
            current_path = Path(current)
            safe_directories: list[str] = []
            for name in sorted(directory_names):
                if name in ignored_directories:
                    continue
                candidate = current_path / name
                linked, unsafe = self._link_status(
                    candidate,
                    reject_all_links=reject_all_links,
                )
                if unsafe:
                    return candidate.relative_to(self.root).as_posix()
                if not linked:
                    safe_directories.append(name)
            directory_names[:] = safe_directories
            for name in sorted(file_names):
                candidate = current_path / name
                _, unsafe = self._link_status(
                    candidate,
                    reject_all_links=reject_all_links,
                )
                if unsafe:
                    return candidate.relative_to(self.root).as_posix()
        return None

    def _link_status(
        self,
        candidate: Path,
        *,
        reject_all_links: bool,
    ) -> tuple[bool, bool]:
        linked = is_link_like(candidate)
        if not linked:
            return False, False
        if reject_all_links:
            return True, True
        try:
            resolve_workspace_path(self.root, candidate)
        except (OSError, ValueError):
            return True, True
        return True, False

    def validate(
        self, project: ProjectProfile, changed_files: Sequence[str], *,
        include_tests: bool = False, profile: Optional[ValidationProfile] = None,
    ) -> ValidationReport:
        try:
            safe_project, safe_changed_files = self._prepare_inputs(
                project,
                changed_files,
                include_tests,
            )
        except (OSError, ValueError) as exc:
            return self._invalid_path_report(str(exc))
        if include_tests:
            external_link = next(
                (
                    unsafe
                    for test_root in safe_project.test_roots
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
            if external_link is not None:
                return self._invalid_path_report(
                    "Validação de testes recusada: symlink ou junction não "
                    f"confinável no workspace: {external_link}"
                )
        effective = profile or self.registry.build_profile(
            safe_project,
            safe_changed_files,
            include_tests,
        )
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
