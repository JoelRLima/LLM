"""Compatibility facade for the canonical process-tree owner."""
# ruff: noqa: F401
from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import agent.process.tree as _canonical
from agent.process.tree import (
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    _POSIX_TERM_GRACE_SECONDS,
    _PROCESS_WAIT_TIMEOUT_SECONDS,
    _WAIT_FAILED,
    _WAIT_OBJECT_0,
    _WAIT_TIMEOUT,
    _WINDOWS_TASKKILL_TIMEOUT_SECONDS,
    Path,
    _configure_windows_api,
    _kill_process_group,
    _terminate_posix_process,
    _wait_for_windows_job,
    _windows_system_directory,
    assign_windows_job,
    close_windows_job,
    create_windows_job,
    os,
    process_group_id,
    signal,
    subprocess,
    terminate_process,
    terminate_windows_job,
)  # noqa: F401

_ORIGINAL_TRUSTED_TASKKILL_PATH = _canonical._trusted_taskkill_path


def _call_with_facade_globals(
    function: Callable[..., Any],
    *args: Any,
    names: tuple[str, ...],
    **kwargs: Any,
) -> Any:
    original = {name: getattr(_canonical, name) for name in names}
    try:
        for name in names:
            setattr(_canonical, name, globals()[name])
        return function(*args, **kwargs)
    finally:
        for name, value in original.items():
            setattr(_canonical, name, value)


def _trusted_taskkill_path() -> str | None:
    return cast(
        str | None,
        _call_with_facade_globals(
            _canonical._trusted_taskkill_path,
            names=("os", "_windows_system_directory"),
        ),
    )


def _terminate_windows_process(process: Any, windows_job: Any) -> str | None:
    return cast(
        str | None,
        _call_with_facade_globals(
            _canonical._terminate_windows_process,
            process,
            windows_job,
            names=("os", "subprocess", "_trusted_taskkill_path", "terminate_windows_job"),
        ),
    )

__all__ = [
    "process_group_id",
    "create_windows_job",
    "assign_windows_job",
    "close_windows_job",
    "terminate_windows_job",
    "terminate_process",
]
