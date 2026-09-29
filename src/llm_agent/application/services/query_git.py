"""Bounded, non-interactive Git observations for workspace queries."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from llm_agent.execution import CommandExecutionError, CommandExecutor, CommandRequest


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
    try:
        result = CommandExecutor().execute(
            CommandRequest(
                executable=executable,
                argv=("-c", "core.fsmonitor=false", "--no-pager", *arguments),
                cwd=workspace,
                environment=_git_environment(),
                timeout_seconds=timeout_seconds,
                cancellation=cancellation,
                stdout_limit=max_output_bytes,
                stderr_limit=max_output_bytes,
            )
        )
    except CommandExecutionError as exc:
        return GitObservation(True, None, "", str(exc))
    return GitObservation(
        True,
        result.return_code,
        result.stdout.decode("utf-8", errors="replace"),
        result.stderr.decode("utf-8", errors="replace"),
        truncated=result.stdout_truncated or result.stderr_truncated,
        cancelled=result.cancelled,
        timed_out=result.timed_out,
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
    from llm_agent.application.services.queries import (
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
    from llm_agent.application.services.queries import (
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
    from llm_agent.application.services.queries import (
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
