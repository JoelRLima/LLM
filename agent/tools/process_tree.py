"""Compatibility facade for the canonical process-tree owner."""
# ruff: noqa: F401
from __future__ import annotations

from agent.process.tree import (
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    _POSIX_TERM_GRACE_SECONDS,
    _PROCESS_WAIT_TIMEOUT_SECONDS,
    _WAIT_FAILED,
    _WAIT_OBJECT_0,
    _WAIT_TIMEOUT,
    _WINDOWS_TASKKILL_TIMEOUT_SECONDS,
    Any,
    Path,
    _configure_windows_api,
    _kill_process_group,
    _terminate_posix_process,
    _terminate_windows_process,
    _trusted_taskkill_path,
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

__all__ = [
    "process_group_id",
    "create_windows_job",
    "assign_windows_job",
    "close_windows_job",
    "terminate_windows_job",
    "terminate_process",
]
