"""Agent validation semantics over the shared Platform command executor."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

from llm_agent.agent.runtime.context import ProcessConcurrencyGate
from llm_agent.cancellation import CancellationToken
from llm_agent.execution import CommandExecutionError, CommandExecutor, CommandRequest
from llm_agent.filesystem.path_safety import resolve_workspace_path

UNSAFE_VALIDATION_ENV = frozenset(
    {
        "COVERAGE_PROCESS_START",
        "COVERAGE_RCFILE",
        "PYTHONBREAKPOINT",
        "PYTHONEXECUTABLE",
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONNOUSERSITE",
        "PYTHONPATH",
        "PYTHONPLATLIBDIR",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
        "PYTHONWARNINGS",
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
    }
)


class ValidationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class CommandSpec:
    name: str
    argv: tuple[str, ...]
    cwd: str = "."
    timeout_seconds: float = 30
    env: Dict[str, str] = field(default_factory=dict)
    workspace_arg_indices: tuple[int, ...] = ()


@dataclass(frozen=True)
class CommandResult:
    name: str
    status: ValidationStatus
    return_code: Optional[int]
    stdout: str
    stderr: str
    duration_seconds: float


class ProcessRunner:
    """Validate command arguments, then delegate lifecycle to Platform."""

    def __init__(
        self,
        root: str | Path,
        cancellation: Optional[CancellationToken] = None,
        max_output_chars: int = 20_000,
        process_gate: Optional[ProcessConcurrencyGate] = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.cancellation = cancellation or CancellationToken()
        self.max_output_chars = max_output_chars
        self.process_gate = process_gate or ProcessConcurrencyGate(1)

    def _resolve_cwd(self, relative: str) -> Path:
        try:
            return resolve_workspace_path(
                self.root,
                relative,
                require_directory=True,
            )
        except ValueError as exc:
            raise ValueError(f"Diretório de comando fora do projeto: {relative}") from exc

    def _validate_workspace_arguments(self, command: CommandSpec) -> None:
        for index in command.workspace_arg_indices:
            if index < 0 or index >= len(command.argv):
                raise ValueError(f"Índice de argumento de workspace inválido: {index}")
            resolve_workspace_path(self.root, command.argv[index])

    def run(self, command: CommandSpec) -> CommandResult:
        started = time.monotonic()
        if not command.argv:
            return CommandResult(command.name, ValidationStatus.UNAVAILABLE, None, "", "Comando vazio.", 0)
        with self.process_gate:
            if self.cancellation.cancelled:
                return CommandResult(command.name, ValidationStatus.CANCELLED, None, "", "cancelled", time.monotonic() - started)
            try:
                self._validate_workspace_arguments(command)
                cwd = self._resolve_cwd(command.cwd)
            except (NotADirectoryError, ValueError) as exc:
                return CommandResult(command.name, ValidationStatus.FAILED, None, "", str(exc), time.monotonic() - started)

            environment = os.environ.copy()
            environment.update(command.env)
            for variable in UNSAFE_VALIDATION_ENV:
                environment.pop(variable, None)
            environment.update(
                {
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                }
            )
            try:
                result = CommandExecutor().execute(
                    CommandRequest(
                        executable=command.argv[0],
                        argv=tuple(command.argv[1:]),
                        cwd=cwd,
                        environment=environment,
                        timeout_seconds=command.timeout_seconds,
                        cancellation=self.cancellation,
                        stdout_limit=self.max_output_chars,
                        stderr_limit=self.max_output_chars,
                    )
                )
            except CommandExecutionError as exc:
                return CommandResult(command.name, ValidationStatus.UNAVAILABLE, None, "", str(exc), time.monotonic() - started)

        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        if result.cancelled:
            status = ValidationStatus.CANCELLED
        elif result.timed_out:
            status = ValidationStatus.TIMED_OUT
        elif result.return_code == 0:
            status = ValidationStatus.PASSED
        else:
            status = ValidationStatus.FAILED
        return CommandResult(
            command.name,
            status,
            result.return_code,
            stdout[-self.max_output_chars :],
            stderr[-self.max_output_chars :],
            time.monotonic() - started,
        )
