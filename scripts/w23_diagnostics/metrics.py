"""Read-only metrics for handles proven to belong to this exact Job Object."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any

from scripts.w23_diagnostics.native import OwnedJob, filetime_seconds, kernel_api
from scripts.w23_windows_job import _winerror


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "read_operations", "write_operations", "other_operations", "read_bytes", "write_bytes", "other_bytes",
    )]


class MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD), *[
        (name, ctypes.c_size_t) for name in (
            "peak_working_set", "working_set", "peak_paged_pool", "paged_pool", "peak_nonpaged_pool",
            "nonpaged_pool", "pagefile", "peak_pagefile", "private_bytes",
        )
    ]]


class JobAccounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_longlong) for name in ("user", "kernel", "period_user", "period_kernel")] + [
        (name, wintypes.DWORD) for name in ("faults", "total_processes", "active_processes", "terminated_processes")
    ] + [("io", IoCounters)]


def metrics_api() -> Any:
    kernel = kernel_api()
    signatures: dict[str, tuple[Any, list[Any]]] = {
        "OpenProcess": (wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
        "IsProcessInJob": (wintypes.BOOL, [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]),
        "GetProcessIoCounters": (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(IoCounters)]),
        "K32GetProcessMemoryInfo": (wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]),
        "QueryFullProcessImageNameW": (
            wintypes.BOOL, [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)],
        ),
    }
    for name, (result, arguments) in signatures.items():
        function = getattr(kernel, name)
        function.restype, function.argtypes = result, arguments
    return kernel


def process_values(kernel: Any, handle: Any) -> dict[str, Any]:
    creation, exit_time, system, user = (wintypes.FILETIME() for _ in range(4))
    if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                  ctypes.byref(system), ctypes.byref(user)):
        raise _winerror("GetProcessTimes observation")
    io, memory = IoCounters(), MemoryCounters()
    memory.cb = ctypes.sizeof(memory)
    if not kernel.GetProcessIoCounters(handle, ctypes.byref(io)):
        raise _winerror("GetProcessIoCounters observation")
    if not kernel.K32GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
        raise _winerror("GetProcessMemoryInfo observation")
    image, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
    if not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)):
        raise _winerror("QueryFullProcessImageName observation")
    basename = image.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
    category = basename if basename in {"powershell.exe", "pwsh.exe", "python.exe", "cmd.exe"} else "other"
    return {"creation_100ns": (creation.dwHighDateTime << 32) | creation.dwLowDateTime,
            "image_category": category, "kernel_seconds": filetime_seconds(system),
            "user_seconds": filetime_seconds(user),
            "io": {field[0]: int(getattr(io, field[0])) for field in io._fields_},
            "working_set_bytes": int(memory.working_set), "private_bytes": int(memory.private_bytes)}


class ProcessSampler:
    def __init__(self) -> None:
        self.kernel = metrics_api()

    def one(self, job: OwnedJob, pid: int) -> dict[str, Any]:
        if pid not in job.pids():
            raise ValueError("PID is not in the owned snapshot")
        handle = self.kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION only, no VM read.
        if not handle:
            raise _winerror("OpenProcess observation")
        try:
            member = wintypes.BOOL()
            if not self.kernel.IsProcessInJob(handle, job.handle, ctypes.byref(member)) or not member.value:
                raise ValueError("Opened process is not in this Job Object")
            # The queried handle pins identity across PID reuse. No PID-based termination.
            return {"pid_owned": pid, "ownership": "IsProcessInJob", **process_values(self.kernel, handle)}
        finally:
            if not self.kernel.CloseHandle(handle):
                raise _winerror("CloseHandle observation")

    def sample(self, job: OwnedJob) -> dict[str, Any]:
        pids = job.pids()
        if len(pids) > 48:
            raise ValueError("Job sample exceeds process cap")
        accounting = JobAccounting()
        if not self.kernel.QueryInformationJobObject(job.handle, 8, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
            raise _winerror("QueryInformationJobObject accounting")
        values, unavailable = [], []
        for pid in pids:
            try:
                values.append(self.one(job, pid))
            except Exception as exc:
                # A short-lived PID can exit between snapshot and open. Keep other measurements;
                # never label this unverified handle/PID as owned or disclose exception contents.
                unavailable.append({"pid_snapshot": pid, "error_type": type(exc).__name__})
        return {"processes": values, "unavailable": unavailable,
                "job": {"kernel_seconds": accounting.kernel / 10_000_000, "user_seconds": accounting.user / 10_000_000,
                        "total_processes": int(accounting.total_processes), "active_processes": int(accounting.active_processes),
                        "io": {field[0]: int(getattr(accounting.io, field[0])) for field in accounting.io._fields_}}}
