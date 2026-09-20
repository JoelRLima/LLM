"""Bounded, non-interactive Git observations for workspace queries."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from typing import Any, Protocol, cast


class _Cancellation(Protocol):
    def is_cancelled(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class GitObservation:
    available: bool
    return_code: int | None
    stdout: str
    stderr: str
    truncated: bool = False
    cancelled: bool = False
    timed_out: bool = False


def _git_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "GIT_EXTERNAL_DIFF": "",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
    )
    return environment


def _drain(stream: Any, parts: list[bytes], limit: int) -> bool:
    total = 0
    truncated = False
    while True:
        chunk = stream.read(8192)
        if not chunk:
            return truncated
        if total < limit:
            parts.append(chunk[: limit - total])
        if total + len(chunk) > limit:
            truncated = True
        total += len(chunk)


def _start_draining(
    process: subprocess.Popen[bytes],
    max_output_bytes: int,
) -> tuple[Thread, Thread, list[bytes], list[bytes], list[bool], list[bool]]:
    assert process.stdout is not None and process.stderr is not None
    stdout_parts: list[bytes] = []
    stderr_parts: list[bytes] = []
    stdout_state = [False]
    stderr_state = [False]

    def drain_stdout() -> None:
        stdout_state[0] = _drain(process.stdout, stdout_parts, max_output_bytes)

    def drain_stderr() -> None:
        stderr_state[0] = _drain(process.stderr, stderr_parts, max_output_bytes)

    stdout_thread = Thread(target=drain_stdout, daemon=False)
    stderr_thread = Thread(target=drain_stderr, daemon=False)
    stdout_thread.start()
    stderr_thread.start()
    return stdout_thread, stderr_thread, stdout_parts, stderr_parts, stdout_state, stderr_state


def _poll_process(
    process: subprocess.Popen[bytes],
    cancellation: _Cancellation,
    timeout_seconds: float,
) -> tuple[bool, bool]:
    deadline = time.monotonic() + timeout_seconds
    while process.poll() is None:
        if cancellation.is_cancelled():
            process.kill()
            return True, False
        if time.monotonic() >= deadline:
            process.kill()
            return False, True
        time.sleep(0.02)
    return False, False


def _settle_threads(stdout_thread: Thread, stderr_thread: Thread) -> bool:
    stdout_thread.join(2)
    stderr_thread.join(2)
    return not (stdout_thread.is_alive() or stderr_thread.is_alive())


def run_git(
    workspace: Path,
    arguments: list[str],
    cancellation: _Cancellation,
    *,
    max_output_bytes: int,
    timeout_seconds: float,
) -> GitObservation:
    executable = shutil.which("git")
    if executable is None:
        return GitObservation(False, None, "", "")
    if cancellation.is_cancelled():
        return GitObservation(True, None, "", "", cancelled=True)
    command = [executable, "-c", "core.fsmonitor=false", "--no-pager", *arguments]
    try:
        process = subprocess.Popen(
            command,
            cwd=str(workspace),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_environment(),
        )
    except OSError as exc:
        return GitObservation(True, None, "", str(exc))
    (
        stdout_thread,
        stderr_thread,
        stdout_parts,
        stderr_parts,
        stdout_state,
        stderr_state,
    ) = _start_draining(process, max_output_bytes)
    cancelled, timed_out = _poll_process(process, cancellation, timeout_seconds)
    try:
        return_code = process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        return GitObservation(True, None, "", "git process did not settle", timed_out=True)
    if not _settle_threads(stdout_thread, stderr_thread):
        return GitObservation(True, return_code, "", "git pipe did not settle", timed_out=True)
    return GitObservation(
        True,
        return_code,
        b"".join(stdout_parts).decode("utf-8", errors="replace"),
        b"".join(stderr_parts).decode("utf-8", errors="replace"),
        truncated=stdout_state[0] or stderr_state[0],
        cancelled=cancelled,
        timed_out=timed_out,
    )


def _git_query(
    service: Any,
    request: Any,
    cancellation: Any,
    arguments: list[str],
    runner: Callable[..., GitObservation],
    max_output_bytes: int,
    timeout_seconds: float,
) -> Any:
    from agent.application_services.queries import (
        MAX_QUERY_OUTPUT_CHARS,
        QUERY_GIT_FAILED,
        QUERY_GIT_UNAVAILABLE,
        QUERY_IO_FAILED,
        QUERY_TIMEOUT,
        _cancelled,
        _error_text,
        _failed,
        _succeeded,
    )

    observation = runner(
        service.workspace.root,
        arguments,
        cancellation,
        max_output_bytes=max_output_bytes,
        timeout_seconds=timeout_seconds,
    )
    if observation.cancelled:
        return _cancelled(request)
    if observation.timed_out:
        return _failed(request, QUERY_TIMEOUT, f"query git: timeout after {timeout_seconds}s")
    if not observation.available:
        return _failed(request, QUERY_GIT_UNAVAILABLE, "query git: git executable unavailable")
    if observation.return_code is None:
        return _failed(request, QUERY_IO_FAILED, _error_text("query git", OSError(observation.stderr)))
    if observation.return_code != 0:
        detail = (observation.stderr or observation.stdout).strip()[:2_000] or "git exited with a non-zero status"
        return _failed(request, QUERY_GIT_FAILED, f"query git: {detail}")
    stdout = observation.stdout
    truncated = observation.truncated or len(stdout) > MAX_QUERY_OUTPUT_CHARS
    return _succeeded(request, stdout[:MAX_QUERY_OUTPUT_CHARS] or "(no output)", truncated=truncated)


def execute_git_status(
    service: Any,
    request: Any,
    cancellation: Any,
    runner: Callable[..., GitObservation],
    max_output_bytes: int,
    timeout_seconds: float,
) -> Any:
    from agent.application_services.queries import (
        WorkspaceQueryKind,
        WorkspaceQueryResult,
        _cancel_requested,
        _cancelled,
    )

    arguments = service._arguments(request, WorkspaceQueryKind.GIT_STATUS)
    if isinstance(arguments, WorkspaceQueryResult):
        return arguments
    if _cancel_requested(cancellation):
        return _cancelled(request)
    return _git_query(service, request, cancellation, ["status", "--short", "--branch", "--untracked-files=normal"], runner, max_output_bytes, timeout_seconds)


def build_diff_arguments(detail: bool, relative_paths: list[str]) -> list[str]:
    arguments = ["diff"] if detail else ["diff", "--numstat"]
    return [*arguments, "--no-ext-diff", "--no-textconv", "--", *relative_paths]


def _parse_stat_value(token: str) -> int | None:
    return None if token == "-" or not token.isdigit() else int(token)


def parse_numstat_output(raw_output: str, *, max_files: int) -> tuple[int, list[dict[str, Any]], bool]:
    rows = [
        {"file": parts[2], "added": _parse_stat_value(parts[0]), "deleted": _parse_stat_value(parts[1])}
        for line in raw_output.splitlines()
        if len(parts := line.split("\t", 2)) == 3
    ]
    return len(rows), rows[:max_files], len(rows) > max_files


def execute_diff(
    service: Any,
    request: Any,
    cancellation: Any,
    runner: Callable[..., GitObservation],
    max_output_bytes: int,
    timeout_seconds: float,
    max_files: int,
) -> Any:
    from agent.application_services.queries import (
        QUERY_PATH_INVALID,
        WorkspaceQueryKind,
        WorkspaceQueryResult,
        WorkspaceQueryStatus,
        _cancel_requested,
        _cancelled,
        _error_text,
        _failed,
        _ResolvedPathError,
        _succeeded,
    )

    arguments = service._arguments(request, WorkspaceQueryKind.DIFF)
    if isinstance(arguments, WorkspaceQueryResult):
        return arguments
    if _cancel_requested(cancellation):
        return _cancelled(request)
    relative_paths: list[str] = []
    for value in cast(tuple[str, ...], arguments["paths"]):
        if value.startswith("-"):
            return _failed(request, QUERY_PATH_INVALID, "query diff: invalid pathspec")
        try:
            relative_paths.append(service._resolve(value).relative_to(service.workspace.root).as_posix())
        except _ResolvedPathError as exc:
            return _failed(request, exc.reason_code, _error_text("query diff", ValueError(exc.message)))
    detail = cast(bool, arguments["detail"])
    result = _git_query(
        service,
        request,
        cancellation,
        build_diff_arguments(detail, relative_paths),
        runner,
        max_output_bytes,
        timeout_seconds,
    )
    if result.status is not WorkspaceQueryStatus.SUCCEEDED or detail:
        return result
    file_count, rows, stat_truncated = parse_numstat_output(str(result.data or ""), max_files=max_files)
    truncated = result.truncated or stat_truncated
    return _succeeded(request, {"file_count": file_count, "files": rows, "truncated": truncated}, truncated=truncated)


__all__ = ["GitObservation", "build_diff_arguments", "execute_diff", "execute_git_status", "parse_numstat_output", "run_git"]
