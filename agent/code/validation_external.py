"""Optional external validation providers built on the canonical process runner."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from agent.code.contracts import ProjectProfile
from agent.code.validation_process import CommandSpec
from agent.code.validation_python import PythonValidationProvider


def _python_files(
    project: ProjectProfile,
    changed_files: Sequence[str],
) -> tuple[str, ...]:
    return PythonValidationProvider.python_files(Path(project.root), changed_files)


class RuffValidationProvider:
    name = "ruff"

    def commands(
        self,
        project: ProjectProfile,
        changed_files: Sequence[str],
        include_tests: bool,
    ) -> tuple[CommandSpec, ...]:
        del include_tests
        files = _python_files(project, changed_files)
        if not files:
            return ()
        argv = ("ruff", "check", "--isolated", "--no-cache", "--no-fix", *files)
        return (
            CommandSpec(
                self.name,
                argv,
                timeout_seconds=60,
                workspace_arg_indices=tuple(range(5, len(argv))),
            ),
        )


class MypyValidationProvider:
    name = "mypy"

    def commands(
        self,
        project: ProjectProfile,
        changed_files: Sequence[str],
        include_tests: bool,
    ) -> tuple[CommandSpec, ...]:
        del include_tests
        files = _python_files(project, changed_files)
        if not files:
            return ()
        argv = ("mypy", "--ignore-missing-imports", *files)
        return (
            CommandSpec(
                self.name,
                argv,
                timeout_seconds=120,
                workspace_arg_indices=tuple(range(2, len(argv))),
            ),
        )


__all__ = ["MypyValidationProvider", "RuffValidationProvider"]
