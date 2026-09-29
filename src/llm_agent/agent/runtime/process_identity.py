"""Small cross-platform process-liveness and identity helpers for runtime locks."""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Protocol


class OwnerStatus(str, Enum):
    """Conservative result of checking a recorded process owner."""

    ALIVE = "alive"
    DEAD = "dead"
    INDETERMINATE = "indeterminate"


class OwnerLiveness(Protocol):
    """Injectable owner check used by the lock and deterministic tests."""

    def check(self, pid: int, process_start_id: str | None) -> OwnerStatus:
        """Return whether the recorded process is alive and the same identity."""


def process_start_id(pid: int) -> str | None:
    """Return a supported-OS identity for ``pid`` when it can be proven."""

    if os.name == "nt":
        status, identity = _windows_process_snapshot(pid)
        return identity if status is OwnerStatus.ALIVE else None
    if Path("/proc").is_dir():
        return _linux_process_start_id(pid)
    return None


def current_process_start_id() -> str | None:
    """Return the current process identity used in newly-created lock records."""

    return process_start_id(os.getpid())


class ProcessOwnerLiveness:
    """Standard-library owner liveness with conservative identity handling."""

    def check(self, pid: int, process_start_id: str | None) -> OwnerStatus:
        if pid <= 0:
            return OwnerStatus.INDETERMINATE
        if os.name == "nt":
            return self._check_windows(pid, process_start_id)
        return self._check_posix(pid, process_start_id)

    @staticmethod
    def _check_posix(pid: int, expected_identity: str | None) -> OwnerStatus:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return OwnerStatus.DEAD
        except PermissionError:
            return OwnerStatus.INDETERMINATE
        except OSError:
            return OwnerStatus.INDETERMINATE

        if Path("/proc").is_dir():
            status, actual_identity = _linux_process_snapshot(pid)
            if status is OwnerStatus.DEAD:
                return OwnerStatus.DEAD
            if status is OwnerStatus.INDETERMINATE:
                return OwnerStatus.INDETERMINATE
        else:
            actual_identity = None
        if expected_identity is None:
            # Legacy records have no identity metadata.  A positively live PID
            # is treated as active rather than risking PID-reuse recovery.
            return OwnerStatus.ALIVE
        if actual_identity is None:
            return OwnerStatus.INDETERMINATE
        return (
            OwnerStatus.ALIVE
            if actual_identity == expected_identity
            else OwnerStatus.DEAD
        )

    @staticmethod
    def _check_windows(pid: int, expected_identity: str | None) -> OwnerStatus:
        status, actual_identity = _windows_process_snapshot(pid)
        if status is not OwnerStatus.ALIVE:
            return status
        if expected_identity is None:
            return OwnerStatus.ALIVE
        if actual_identity is None:
            return OwnerStatus.INDETERMINATE
        return (
            OwnerStatus.ALIVE
            if actual_identity == expected_identity
            else OwnerStatus.DEAD
        )


def _linux_process_start_id(pid: int) -> str | None:
    """Use the kernel boot identity and ``/proc`` start tick to detect reuse."""

    status, identity = _linux_process_snapshot(pid)
    return identity if status is OwnerStatus.ALIVE else None


def _linux_process_snapshot(pid: int) -> tuple[OwnerStatus, str | None]:
    """Read Linux process state and identity without treating unknown as dead."""

    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        terminal_status, _ = _parse_linux_process_stat(stat_text, "")
        if terminal_status is OwnerStatus.DEAD:
            return OwnerStatus.DEAD, None
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii"
        ).strip()
        return _parse_linux_process_stat(stat_text, boot_id)
    except (OSError, UnicodeError, ValueError):
        return OwnerStatus.INDETERMINATE, None


def _parse_linux_process_stat(stat_text: str, boot_id: str) -> tuple[OwnerStatus, str | None]:
    """Parse ``/proc/<pid>/stat`` state and start tick conservatively."""

    closing_parenthesis = stat_text.rfind(")")
    if closing_parenthesis < 0:
        return OwnerStatus.INDETERMINATE, None
    fields = stat_text[closing_parenthesis + 2 :].split()
    if not fields:
        return OwnerStatus.INDETERMINATE, None
    state = fields[0]
    if state in {"Z", "X", "x"}:
        return OwnerStatus.DEAD, None
    if state not in {"R", "S", "D", "T", "t", "W", "I"}:
        return OwnerStatus.INDETERMINATE, None
    if len(fields) <= 19 or not boot_id or not fields[19].isdigit():
        return OwnerStatus.INDETERMINATE, None
    return OwnerStatus.ALIVE, f"linux:{boot_id}:{fields[19]}"


def _windows_process_snapshot(pid: int) -> tuple[OwnerStatus, str | None]:
    """Read Windows liveness and creation time through kernel32 only."""

    if os.name != "nt":
        return OwnerStatus.INDETERMINATE, None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

        class _FileTime(ctypes.Structure):  # type: ignore[misc,valid-type]
            _fields_ = [
                ("low", wintypes.DWORD),
                ("high", wintypes.DWORD),
            ]

        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime),
            ctypes.POINTER(_FileTime),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            error = ctypes.get_last_error()  # type: ignore[attr-defined]
            if error in {6, 87}:  # ERROR_INVALID_HANDLE / ERROR_INVALID_PARAMETER
                return OwnerStatus.DEAD, None
            return OwnerStatus.INDETERMINATE, None
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                error = ctypes.get_last_error()  # type: ignore[attr-defined]
                return (
                    (OwnerStatus.DEAD, None)
                    if error in {6, 87}
                    else (OwnerStatus.INDETERMINATE, None)
                )
            if exit_code.value != 259:  # STILL_ACTIVE
                return OwnerStatus.DEAD, None
            creation = _FileTime()
            exit_time = _FileTime()
            kernel_time = _FileTime()
            user_time = _FileTime()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return OwnerStatus.INDETERMINATE, None
            value = (creation.high << 32) | creation.low
            if value == 0:
                return OwnerStatus.INDETERMINATE, None
            return OwnerStatus.ALIVE, f"windows:{value}"
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return OwnerStatus.INDETERMINATE, None


__all__ = [
    "OwnerLiveness",
    "OwnerStatus",
    "ProcessOwnerLiveness",
    "current_process_start_id",
    "process_start_id",
]
