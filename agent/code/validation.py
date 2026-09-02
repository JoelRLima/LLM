"""Project validation profiles and diagnostic aggregation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional, Protocol, Sequence

from agent.cancellation import CancellationToken
from agent.code.contracts import Diagnostic, DiagnosticSeverity, ProjectProfile
from agent.code.discovery import IGNORED_DIRECTORIES
from agent.code.validation_external import (
    MypyValidationProvider,
    RuffValidationProvider,
)
from agent.code.validation_preflight import find_external_link, link_status
from agent.code.validation_process import (
    CommandResult,
    CommandSpec,
    ProcessRunner,
    ValidationStatus,
)
from agent.code.validation_python import PythonValidationProvider
from agent.runtime.context import ProcessConcurrencyGate
from agent.runtime.path_safety import (
    resolve_workspace_path,
    workspace_relative_path,
)

__all__ = [
    "CommandResult", "CommandSpec", "ProcessRunner", "ProjectValidator",
    "ValidationProfile", "ValidationReport", "ValidationRegistry", "ValidationStatus",
    "MypyValidationProvider", "RuffValidationProvider",
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
        self, project: ProjectProfile, changed_files: Sequence[str], include_tests: bool = False
    ) -> ValidationProfile:
        tests_requested = bool(include_tests or (
            self.validation_config.get("enabled", True)
            and self.validation_config.get("pytest") is True
        ))
        commands = [
            command for provider in self.providers
            for command in provider.commands(project, changed_files, tests_requested)
        ]
        return ValidationProfile(tuple(commands))


class ProjectValidator:
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

    def validate(
        self, project: ProjectProfile, changed_files: Sequence[str], *,
        include_tests: bool = False, profile: Optional[ValidationProfile] = None,
    ) -> ValidationReport:
        configured_tests = (
            self.registry.validation_config.get("enabled", True)
            and self.registry.validation_config.get("pytest") is True
        )
        effective_include_tests = bool(include_tests or configured_tests)
        try:
            safe_project, safe_changed_files = self._prepare_inputs(
                project,
                changed_files,
                effective_include_tests,
            )
        except (OSError, ValueError) as exc:
            return self._invalid_path_report(str(exc))
        link_failure = self._test_link_failure(safe_project, effective_include_tests)
        if link_failure is not None:
            return link_failure
        effective = self._effective_profile(
            safe_project, safe_changed_files, effective_include_tests, profile
        )
        return self._run_profile(effective)

    def _test_link_failure(
        self, project: ProjectProfile, include_tests: bool,
    ) -> ValidationReport | None:
        if not include_tests:
            return None
        external_link = next(
            (
                unsafe
                for test_root in project.test_roots
                if (
                    unsafe := self._find_external_link(
                        resolve_workspace_path(
                            self.root, test_root, require_directory=True,
                        ),
                        ignored_directories=frozenset(),
                        reject_all_links=True,
                    )
                ) is not None
            ),
            None,
        )
        external_link = external_link or self._find_external_link()
        if external_link is None:
            return None
        return self._invalid_path_report(
            "Validação de testes recusada: symlink ou junction não "
            f"confinável no workspace: {external_link}"
        )

    def _effective_profile(
        self,
        project: ProjectProfile,
        changed_files: Sequence[str],
        include_tests: bool,
        profile: Optional[ValidationProfile],
    ) -> ValidationProfile:
        minimum = self.registry.build_profile(project, changed_files, include_tests)
        if profile is None:
            return minimum
        commands = list(profile.commands)
        commands.extend(command for command in minimum.commands if command not in commands)
        return ValidationProfile(tuple(commands))

    def _run_profile(self, profile: ValidationProfile) -> ValidationReport:
        if not profile.commands:
            return ValidationReport(ValidationStatus.UNAVAILABLE, ())
        results: list[CommandResult] = []
        diagnostics: list[Diagnostic] = []
        for command in profile.commands:
            result = self.runner.run(command)
            results.append(result)
            if diagnostic := self._diagnostic(result):
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
