"""Create a suspended Windows process, contain it, then resume; no PID killing."""
from __future__ import annotations

import ctypes
import importlib
import os
import shutil
import subprocess
from ctypes import wintypes
from pathlib import Path
from typing import Any, BinaryIO, cast

from scripts.w23_windows_job import ExtendedLimitInformation, _api, _winerror

KILL_ON_CLOSE = 0x2000
LIMITS = KILL_ON_CLOSE | 0x100 | 0x200 | 0x8
SUSPENDED_NO_WINDOW = 0x4 | 0x08000000 | 0x00080000


class JobPids(ctypes.Structure):
    _fields_ = [("assigned", wintypes.DWORD), ("listed", wintypes.DWORD), ("pids", ctypes.c_size_t * 64)]


def kernel_api() -> Any:
    kernel = _api()
    signatures: dict[str, tuple[Any, list[Any]]] = {
        "ResumeThread": (wintypes.DWORD, [wintypes.HANDLE]),
        "TerminateProcess": (wintypes.BOOL, [wintypes.HANDLE, wintypes.UINT]),
        "WaitForSingleObject": (wintypes.DWORD, [wintypes.HANDLE, wintypes.DWORD]),
        "GetExitCodeProcess": (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]),
        "GetProcessTimes": (wintypes.BOOL, [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]),
    }
    for name, (result, arguments) in signatures.items():
        function = getattr(kernel, name)
        function.restype, function.argtypes = result, arguments
    return kernel


def filetime_seconds(item: Any) -> float:
    return float((item.dwHighDateTime << 32) | item.dwLowDateTime) / 10_000_000


class OwnedJob:
    def __init__(self, memory_mb: int = 4096, process_mb: int = 3072) -> None:
        if os.name != "nt":
            raise OSError("native Windows required")
        self.kernel = kernel_api()
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise _winerror("CreateJobObjectW")
        try:
            self._limits(memory_mb, process_mb)
        except BaseException:
            self.close()
            raise

    def _limits(self, memory_mb: int, process_mb: int) -> None:
        info = ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = LIMITS
        info.BasicLimitInformation.ActiveProcessLimit = 48
        info.JobMemoryLimit = memory_mb * 1024 * 1024
        info.ProcessMemoryLimit = process_mb * 1024 * 1024
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise _winerror("SetInformationJobObject")

    def terminate(self, code: int) -> None:
        if not self.kernel.TerminateJobObject(self.handle, code):
            raise _winerror("TerminateJobObject")

    def pids(self) -> list[int]:
        info = JobPids()
        if not self.kernel.QueryInformationJobObject(self.handle, 3, ctypes.byref(info), ctypes.sizeof(info), None):
            raise _winerror("QueryInformationJobObject PID list")
        if info.assigned > 64 or info.listed > 64:
            raise OSError("owned PID snapshot exceeded capacity")
        return [int(info.pids[index]) for index in range(info.listed)]

    def close(self) -> None:
        if self.handle:
            handle, self.handle = self.handle, None
            if not self.kernel.CloseHandle(handle):
                raise _winerror("CloseHandle Job")


class NativeChild:
    def __init__(self, job: OwnedJob, command: list[str], cwd: Path, env: dict[str, str],
                 stdout_fd: int, stderr_fd: int, stdin: BinaryIO) -> None:
        _winapi = importlib.import_module("_winapi")
        msvcrt = importlib.import_module("msvcrt")

        self.job = job
        self.process: Any = None
        self.thread: Any = None
        handles = [msvcrt.get_osfhandle(fd) for fd in (stdin.fileno(), stdout_fd, stderr_fd)]
        # Windows-only members are absent from Linux typing configurations.
        startup = cast(Any, subprocess).STARTUPINFO()
        startup.dwFlags |= cast(Any, subprocess).STARTF_USESTDHANDLES
        startup.hStdInput, startup.hStdOutput, startup.hStdError = handles
        startup.lpAttributeList = {"handle_list": handles}
        inherited: list[int] = []
        try:
            for handle in handles:
                cast(Any, os).set_handle_inheritable(handle, True)
                inherited.append(handle)
            executable = shutil.which(command[0], path=env.get("PATH"))
            if executable is None or Path(executable).suffix.lower() != ".exe":
                raise OSError("native executable could not be resolved")
            self.process, self.thread, self.pid, self.tid = _winapi.CreateProcess(
                executable, subprocess.list2cmdline(command), None, None, True,
                SUSPENDED_NO_WINDOW, env, str(cwd), startup,
            )
            self._contain_and_resume()
        except BaseException:
            self.abort()
            raise
        finally:
            for handle in inherited:
                cast(Any, os).set_handle_inheritable(handle, False)

    def _contain_and_resume(self) -> None:
        if not self.job.kernel.AssignProcessToJobObject(self.job.handle, self.process):
            raise _winerror("AssignProcessToJobObject suspended child")
        if self.job.kernel.ResumeThread(self.thread) == 0xFFFFFFFF:
            raise _winerror("ResumeThread")
        if not self.job.kernel.CloseHandle(self.thread):
            raise _winerror("CloseHandle primary thread")
        self.thread = None

    def poll(self) -> int | None:
        state = self.job.kernel.WaitForSingleObject(self.process, 0)
        if state == 0x102:
            return None
        if state != 0:
            raise _winerror("WaitForSingleObject")
        code = wintypes.DWORD()
        if not self.job.kernel.GetExitCodeProcess(self.process, ctypes.byref(code)):
            raise _winerror("GetExitCodeProcess")
        return int(code.value)

    def cpu(self) -> dict[str, float]:
        creation, exit_time, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not self.job.kernel.GetProcessTimes(self.process, ctypes.byref(creation), ctypes.byref(exit_time),
                                               ctypes.byref(kernel), ctypes.byref(user)):
            raise _winerror("GetProcessTimes")
        return {"kernel_seconds": filetime_seconds(kernel), "user_seconds": filetime_seconds(user)}

    def abort(self) -> None:
        if self.process:
            self.job.kernel.TerminateProcess(self.process, 125)
        self.close()

    def close(self) -> None:
        for name in ("thread", "process"):
            handle = getattr(self, name)
            if handle:
                setattr(self, name, None)
                if not self.job.kernel.CloseHandle(handle):
                    raise _winerror("CloseHandle child")
