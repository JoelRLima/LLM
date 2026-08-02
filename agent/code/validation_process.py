from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

from agent.cancellation import CancellationToken
from agent.code.path_safety import resolve_workspace_path
from agent.runtime.context import ProcessConcurrencyGate

WINDOWS_NEW_PROCESS_GROUP = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
UNSAFE_VALIDATION_ENV = frozenset(
    {
        "COVERAGE_PROCESS_START",
        "COVERAGE_RCFILE",
        "PYTHONBREAKPOINT",
        "PYTHONEXECUTABLE",
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONPATH",
        "PYTHONPLATLIBDIR",
        "PYTHONPYCACHEPREFIX",
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
    def __init__(
        self, root: str | Path, cancellation: Optional[CancellationToken] = None,
        max_output_chars: int = 20_000, process_gate: Optional[ProcessConcurrencyGate] = None,
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
                raise ValueError(
                    f"Índice de argumento de workspace inválido: {index}"
                )
            resolve_workspace_path(self.root, command.argv[index])

    @staticmethod
    def _terminate(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            killpg = getattr(os, "killpg", None)
            getpgid = getattr(os, "getpgid", None)
            if os.name != "nt" and callable(killpg) and callable(getpgid):
                killpg(getpgid(process.pid), signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass

    def run(self, command: CommandSpec) -> CommandResult:
        if not command.argv:
            return CommandResult(command.name, ValidationStatus.UNAVAILABLE, None, "", "Comando vazio.", 0)
        started = time.monotonic()
        with self.process_gate:
            if self.cancellation.cancelled:
                return CommandResult(command.name, ValidationStatus.CANCELLED, None, "", "cancelled", time.monotonic() - started)
            process = self._start(command, started)
            if isinstance(process, CommandResult):
                return process
            status, stdout, stderr = self._wait(process, command.timeout_seconds, started)
        return CommandResult(
            command.name, status, process.returncode,
            stdout[-self.max_output_chars:], stderr[-self.max_output_chars:], time.monotonic() - started,
        )

    def _start(self, command: CommandSpec, started: float) -> subprocess.Popen[str] | CommandResult:
        environment = os.environ.copy()
        environment.update(command.env)
        for variable in UNSAFE_VALIDATION_ENV:
            environment.pop(variable, None)
        environment.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            }
        )
        try:
            self._validate_workspace_arguments(command)
            cwd = self._resolve_cwd(command.cwd)
            return subprocess.Popen(
                list(command.argv), cwd=cwd, env=environment,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, shell=False,
                start_new_session=os.name != "nt",
                creationflags=WINDOWS_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
        except FileNotFoundError as exc:
            return CommandResult(command.name, ValidationStatus.UNAVAILABLE, None, "", str(exc), time.monotonic() - started)
        except (NotADirectoryError, ValueError) as exc:
            return CommandResult(
                command.name,
                ValidationStatus.FAILED,
                None,
                "",
                str(exc),
                time.monotonic() - started,
            )

    def _wait(
        self, process: subprocess.Popen[str], timeout: float, started: float
    ) -> tuple[ValidationStatus, str, str]:
        while True:
            if self.cancellation.cancelled:
                self._terminate(process)
                stdout, stderr = process.communicate()
                return ValidationStatus.CANCELLED, stdout, stderr
            if time.monotonic() - started > timeout:
                self._terminate(process)
                stdout, stderr = process.communicate()
                return ValidationStatus.TIMED_OUT, stdout, stderr
            try:
                stdout, stderr = process.communicate(timeout=0.1)
                status = ValidationStatus.PASSED if process.returncode == 0 else ValidationStatus.FAILED
                return status, stdout, stderr
            except subprocess.TimeoutExpired:
                continue
