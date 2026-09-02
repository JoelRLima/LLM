"""Process-tree lifecycle helpers for external tool extensions."""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Any

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_WAIT_OBJECT_0, _WAIT_TIMEOUT, _WAIT_FAILED = 0, 258, 0xFFFFFFFF
_WINDOWS_TASKKILL_TIMEOUT_SECONDS = 2
_PROCESS_WAIT_TIMEOUT_SECONDS = 1
_POSIX_TERM_GRACE_SECONDS = 0.5
def _trusted_taskkill_path() -> str | None:
    """Return the canonical Windows taskkill executable, never a bare name."""

    if os.name != "nt":
        return None
    system_directory = _windows_system_directory()
    if not system_directory:
        return None
    system32 = Path(system_directory)
    candidate = system32 / "taskkill.exe"
    try:
        resolved = candidate.resolve(strict=True)
        if not resolved.is_file() or str(resolved.parent).casefold() != str(system32).casefold():
            return None
        return str(resolved)
    except (OSError, ValueError):
        return None
def _windows_system_directory() -> str | None:
    """Read the system directory from Win32, never from inherited env vars."""

    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        get_system_directory = kernel32.GetSystemDirectoryW
        get_system_directory.argtypes = [wintypes.LPWSTR, wintypes.UINT]
        get_system_directory.restype = wintypes.UINT
        size = 260
        while size <= 32768:
            buffer = ctypes.create_unicode_buffer(size)
            length = int(get_system_directory(buffer, size))
            if length == 0:
                return None
            if length < size - 1:
                return buffer.value
            size *= 2
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    return None


def process_group_id(process: subprocess.Popen[Any]) -> int | None:
    if os.name == "nt":
        return None
    try:
        getpgid = getattr(os, "getpgid", None)
        return getpgid(process.pid) if getpgid is not None else process.pid
    except OSError:
        return process.pid
def _configure_windows_api(kernel32: Any, ctypes: Any, wintypes: Any) -> None:
    """Declare the Win32 ABI explicitly so HANDLEs are never truncated."""

    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
def create_windows_job() -> Any:
    """Create a kill-on-close job object for an extension process tree."""

    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [("values", ctypes.c_ulonglong * 6)]

        class _ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimitInformation),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        _configure_windows_api(kernel32, ctypes, wintypes)
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            kernel32.CloseHandle(handle)
            return None
        return kernel32, handle
    except (AttributeError, OSError, TypeError, ValueError):
        return None
def assign_windows_job(job: Any, process: subprocess.Popen[Any]) -> bool:
    if job is None:
        return False
    kernel32, handle = job
    try:
        process_handle = getattr(process, "_handle", None)
        if process_handle is None:
            return False
        return bool(kernel32.AssignProcessToJobObject(handle, process_handle))
    except (AttributeError, OSError, TypeError, ValueError):
        return False
def close_windows_job(job: Any) -> bool:
    if job is None:
        return True
    kernel32, handle = job
    try:
        return bool(kernel32.CloseHandle(handle))
    except (AttributeError, OSError, TypeError, ValueError):
        return False
def terminate_windows_job(job: Any) -> bool:
    if job is None:
        return False
    kernel32, handle = job
    try:
        return bool(kernel32.TerminateJobObject(handle, 1))
    except (AttributeError, OSError, TypeError, ValueError):
        return False
def _wait_for_windows_job(job: Any, timeout_seconds: float) -> str | None:
    if job is None:
        return None
    kernel32, handle = job
    try:
        result = int(kernel32.WaitForSingleObject(handle, int(timeout_seconds * 1000)))
    except (AttributeError, OSError, TypeError, ValueError):
        return "falha ao aguardar terminacao do Job Object"
    if result == _WAIT_OBJECT_0:
        return None
    if result == _WAIT_TIMEOUT:
        return "Job Object nao confirmou terminacao da arvore"
    if result == _WAIT_FAILED:
        return "WaitForSingleObject falhou ao confirmar terminacao da arvore"
    return f"WaitForSingleObject retornou {result} ao confirmar terminacao da arvore"
def _kill_process_group(group_id: int, sig: int) -> str | None:
    killpg = getattr(os, "killpg", None)
    if killpg is None:
        return "killpg indisponivel"
    try:
        killpg(group_id, sig)
    except ProcessLookupError:
        # The group can disappear between SIGTERM and SIGKILL.
        return None
    except PermissionError as exc:
        return f"permissao negada ao sinalizar grupo {group_id}: {exc}"
    except OSError as exc:
        return f"falha ao sinalizar grupo {group_id}: {exc}"
    return None

def _terminate_posix_process(
    process: subprocess.Popen[Any], process_group_id: int | None
) -> str | None:
    killpg = getattr(os, "killpg", None)
    if killpg is None:
        try:
            process.terminate()
            process.wait(timeout=_POSIX_TERM_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
        except OSError as exc:
            return f"falha ao terminar processo POSIX: {exc}"
        try:
            process.wait(timeout=_PROCESS_WAIT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            return "processo POSIX nao terminou apos escalada"
        return None

    group_id = process_group_id if process_group_id is not None else process.pid
    term_error = _kill_process_group(group_id, signal.SIGTERM)
    try:
        process.wait(timeout=_POSIX_TERM_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    # The parent may already have exited while descendants keep the group or
    # inherited pipes alive; SIGKILL is intentionally sent to the known group.
    kill_error = _kill_process_group(group_id, getattr(signal, "SIGKILL", signal.SIGTERM))
    try:
        process.wait(timeout=_PROCESS_WAIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        # Emergency parent reap only; the normal escalation above is group-wide.
        try:
            process.kill()
            process.wait(timeout=_PROCESS_WAIT_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            return "processo POSIX nao terminou apos SIGKILL"
    if kill_error is not None:
        return kill_error
    return term_error

def _terminate_windows_process(
    process: subprocess.Popen[Any], windows_job: Any
) -> str | None:
    job_terminated = terminate_windows_job(windows_job)
    job_wait_error = (
        _wait_for_windows_job(windows_job, _PROCESS_WAIT_TIMEOUT_SECONDS)
        if job_terminated
        else None
    )
    taskkill_failed = False
    if not job_terminated or job_wait_error is not None or process.poll() is None:
        taskkill = _trusted_taskkill_path()
        try:
            if taskkill is None:
                raise FileNotFoundError("taskkill.exe confiavel indisponivel")
            completed = subprocess.run(
                [taskkill, "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=_WINDOWS_TASKKILL_TIMEOUT_SECONDS,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            )
            taskkill_failed = completed.returncode != 0
        except (OSError, subprocess.TimeoutExpired):
            taskkill_failed = True
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.wait(timeout=_PROCESS_WAIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=_PROCESS_WAIT_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            return "processo Windows nao terminou apos escalada"
    if windows_job is not None and (not job_terminated or job_wait_error is not None):
        job_wait_error = _wait_for_windows_job(windows_job, _PROCESS_WAIT_TIMEOUT_SECONDS)
        if job_wait_error is not None:
            return job_wait_error
    if taskkill_failed and not job_terminated:
        return "Job Object indisponivel e taskkill nao confirmou cleanup da arvore"
    return None

def terminate_process(
    process: subprocess.Popen[Any],
    windows_job: Any = None,
    *,
    process_group_id: int | None = None,
) -> str | None:
    """Terminate a process tree and collect the parent.

    ``None`` means the parent was reaped and the requested tree termination did
    not report an error. A string is an observable cleanup failure.
    """

    if os.name != "nt":
        return _terminate_posix_process(process, process_group_id)
    return _terminate_windows_process(process, windows_job)
