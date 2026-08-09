"""Bounded process runner used by the model-actionable shell skill."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

from agent.cancellation import is_cancellation_requested
from agent.tools.process_tree import (
    assign_windows_job,
    close_windows_job,
    create_windows_job,
    process_group_id,
    terminate_process,
)
from agent.tools.stdio_streams import StreamCapture, close_pipes, start_readers

MAX_OUTPUT_BYTES = 1_048_576
MAX_STDERR_BYTES = 1_048_576
_PATH_SEPARATOR = os.pathsep


@dataclass(frozen=True)
class ShellProcessError(Exception):
    status: str
    detail: str


def _reader_failure(readers: list[Any], reader_errors: list[str], stdout: StreamCapture, stderr: StreamCapture) -> str | None:
    if any(reader.is_alive() for reader in readers):
        return "Threads de drenagem shell nao terminaram."
    if reader_errors:
        return "Falha ao ler a saida do shell."
    if stdout.exceeded or stderr.exceeded:
        return "Limite de stdout/stderr excedido."
    return None


def _monitor_process(process: subprocess.Popen[Any], stdout: StreamCapture, stderr: StreamCapture, timeout: int, cancellation_token: Any | None, cancellation_event: Event | None) -> ShellProcessError | None:
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        if is_cancellation_requested(cancellation_token, cancellation_event):
            return ShellProcessError("cancelled", "Execucao shell cancelada.")
        if stdout.exceeded or stderr.exceeded:
            return ShellProcessError("failed", "Limite de stdout/stderr excedido.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return ShellProcessError("timed_out", f"Timeout apos {timeout}s.")
        try:
            process.wait(timeout=min(0.05, remaining))
        except subprocess.TimeoutExpired:
            continue
    return None


def _stop_readers(readers: list[Any], stop_readers: Event | None) -> None:
    if stop_readers is not None:
        stop_readers.set()
    for reader in readers:
        reader.join(timeout=1.0)


def _resolve_executable(command: str, environment: dict[str, str]) -> str | None:
    """Resolve an allowlisted executable without implicit cwd shadowing."""

    command_path = Path(command)
    if command_path.is_absolute():
        candidates = [command_path]
    else:
        raw_path = environment.get("PATH", "")
        path_entries = [
            Path(entry)
            for entry in raw_path.split(_PATH_SEPARATOR)
            if entry and Path(entry).is_absolute()
        ]
        if os.name == "nt" and not command_path.suffix:
            names = [
                command + extension
                for extension in environment.get("PATHEXT", "").split(_PATH_SEPARATOR)
                if extension
            ]
        else:
            names = [command]
        candidates = [directory / name for directory in path_entries for name in names]

    for candidate in candidates:
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if os.name == "nt":
            pathext = {
                extension.casefold()
                for extension in environment.get("PATHEXT", "").split(_PATH_SEPARATOR)
                if extension
            }
            if resolved.suffix.casefold() not in pathext:
                continue
        elif not os.access(resolved, os.X_OK):
            continue
        return str(resolved)
    return None


def run_bounded_process(
    argv: list[str], *, workspace: Any, environment: dict[str, str], timeout: int,
    cancellation_token: Any | None = None, cancellation_event: Event | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run an allowlisted command with concurrent bounded stream drains."""

    selected_argv = list(argv)
    executable = _resolve_executable(selected_argv[0], environment)
    if executable is None:
        raise FileNotFoundError(selected_argv[0])
    selected_argv[0] = executable
    windows_job = create_windows_job()
    if os.name == "nt" and windows_job is None:
        raise ShellProcessError("unavailable", "Job Object indisponivel para o processo shell.")
    process: subprocess.Popen[Any] | None = None
    process_group: int | None = None
    readers: list[Any] = []
    stop_readers: Event | None = None
    stdout: StreamCapture | None = None
    stderr: StreamCapture | None = None
    try:
        process = subprocess.Popen(
            selected_argv, shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=workspace, env=environment, text=False,
            start_new_session=os.name != "nt",
            creationflags=(int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0),
        )
        process_group = process_group_id(process)
        if os.name == "nt" and not assign_windows_job(windows_job, process):
            termination_error = terminate_process(
                process,
                windows_job,
                process_group_id=process_group,
            )
            if termination_error is not None:
                raise ShellProcessError(
                    "unavailable",
                    "Falha ao associar o processo shell ao Job Object; "
                    f"cleanup falhou: {termination_error}",
                )
            raise ShellProcessError(
                "unavailable",
                "Falha ao associar o processo shell ao Job Object.",
            )
        readers, stdout, stderr, stop_readers, reader_errors = start_readers(
            process, MAX_OUTPUT_BYTES, MAX_STDERR_BYTES
        )
        failure = _monitor_process(process, stdout, stderr, timeout, cancellation_token, cancellation_event)
        if failure is not None:
            termination_error = terminate_process(process, windows_job, process_group_id=process_group)
            if termination_error is not None:
                raise ShellProcessError("unavailable", f"Cleanup shell falhou: {termination_error}")
            raise failure
        process.wait()
        stop_readers.set()
        _stop_readers(readers, stop_readers)
        reader_error = _reader_failure(readers, reader_errors, stdout, stderr)
        if reader_error is not None:
            raise ShellProcessError("failed", reader_error)
        return subprocess.CompletedProcess(
            argv, process.returncode,
            bytes(stdout.content).decode("utf-8", errors="replace"),
            bytes(stderr.content).decode("utf-8", errors="replace"),
        )
    finally:
        if stop_readers is not None:
            stop_readers.set()
        if process is not None:
            close_pipes(process)
        _stop_readers(readers, stop_readers)
        close_windows_job(windows_job)


__all__ = ["MAX_OUTPUT_BYTES", "MAX_STDERR_BYTES", "ShellProcessError", "run_bounded_process"]
