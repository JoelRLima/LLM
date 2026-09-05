"""Built-in Python syntax and test validation profile."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

from agent.code.contracts import ProjectProfile
from agent.code.validation_process import CommandSpec
from agent.runtime.path_safety import (
    resolve_workspace_path,
    workspace_command_argument,
)

_SYNTAX_VALIDATION_CODE = (
    "import pathlib,sys;"
    "[compile(pathlib.Path(path).read_bytes(),path,'exec') "
    "for path in sys.argv[1:]]"
)
_PYTEST_VALIDATION_CODE = (
    "import pathlib,sys,tempfile,pytest;"
    "sys.path.insert(0,str(pathlib.Path.cwd()));"
    "temporary=tempfile.TemporaryDirectory("
    "prefix='.llm-agent-pytest-',dir='.');"
    "config=pathlib.Path(temporary.name)/'pytest.ini';"
    "config.write_text('[pytest]\\n',encoding='utf-8');"
    "raise SystemExit(pytest.main(["
    "'-q','-p','no:cacheprovider','--rootdir=.','--confcutdir=.',"
    "'-c',str(config),*sys.argv[1:]]))"
)


class PythonValidationProvider:
    name = "python"

    def commands(
        self,
        project: ProjectProfile,
        changed_files: Sequence[str],
        include_tests: bool,
        selected_test_files: Sequence[str] = (),
    ) -> tuple[CommandSpec, ...]:
        if "python" not in project.languages:
            return ()
        root = Path(project.root)
        commands: list[CommandSpec] = []
        python_files = self.python_files(root, changed_files)
        if python_files:
            commands.append(self._syntax_command(python_files))
        if include_tests:
            test_files = self._test_files(root, selected_test_files)
            if test_files:
                commands.append(self._pytest_command(test_files))
        return tuple(commands)

    @staticmethod
    def python_files(
        root: Path,
        changed_files: Sequence[str],
    ) -> tuple[str, ...]:
        python_files: list[str] = []
        for path in changed_files:
            try:
                resolved = resolve_workspace_path(
                    root,
                    path,
                    require_file=True,
                )
            except (OSError, ValueError):
                continue
            if resolved.suffix.lower() in {".py", ".pyi"}:
                python_files.append(workspace_command_argument(root, resolved))
        return tuple(python_files)

    @staticmethod
    def _test_files(
        root: Path,
        test_files: Sequence[str],
    ) -> tuple[str, ...]:
        safe_files: list[str] = []
        for test_file in test_files:
            try:
                resolved = resolve_workspace_path(
                    root,
                    test_file,
                    require_file=True,
                )
            except (OSError, ValueError):
                continue
            safe_files.append(workspace_command_argument(root, resolved))
        return tuple(safe_files)

    @staticmethod
    def _syntax_command(python_files: Sequence[str]) -> CommandSpec:
        argv = (
            sys.executable,
            "-I",
            "-B",
            "-c",
            _SYNTAX_VALIDATION_CODE,
            *python_files,
        )
        return CommandSpec(
            "python-syntax",
            argv,
            timeout_seconds=20,
            workspace_arg_indices=tuple(range(5, len(argv))),
        )

    @staticmethod
    def _pytest_command(test_files: Sequence[str]) -> CommandSpec:
        argv = (
            sys.executable,
            "-B",
            "-c",
            _PYTEST_VALIDATION_CODE,
            *test_files,
        )
        return CommandSpec(
            "pytest",
            argv,
            timeout_seconds=120,
            workspace_arg_indices=tuple(range(4, len(argv))),
        )
