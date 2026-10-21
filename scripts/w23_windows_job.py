"""Owned Windows Job Object resource limits for CI acceptance."""
from __future__ import annotations

import ctypes
import subprocess
from ctypes import wintypes
from typing import Any

JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000

class BasicLimitInformation(ctypes.Structure):
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


class IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", BasicLimitInformation),
        ("IoInfo", IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class BasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


def _native_api(name: str) -> Any:
    function = getattr(ctypes, name, None)
    if function is None:
        raise OSError("native Windows ctypes API is unavailable")
    return function


def _api() -> Any:
    kernel = _native_api("WinDLL")("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.SetPriorityClass.restype = wintypes.BOOL
    return kernel


def _winerror(message: str) -> OSError:
    error = _native_api("get_last_error")()
    description = str(_native_api("FormatError")(error)).strip()
    return OSError(error, f"{message}: {description}")


def _create_limited_job(memory_mb: int, process_memory_mb: int, max_processes: int) -> tuple[Any, Any]:
    kernel = _api()
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise _winerror("CreateJobObjectW failed")
    info = ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = (
        JOB_OBJECT_LIMIT_JOB_MEMORY
        | JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
    )
    info.BasicLimitInformation.ActiveProcessLimit = max_processes
    info.JobMemoryLimit = memory_mb * 1024 * 1024
    info.ProcessMemoryLimit = process_memory_mb * 1024 * 1024
    if not kernel.SetInformationJobObject(
        job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info),
    ):
        kernel.CloseHandle(job)
        raise _winerror("SetInformationJobObject failed")
    current = kernel.GetCurrentProcess()
    if not kernel.AssignProcessToJobObject(job, current):
        kernel.CloseHandle(job)
        raise _winerror("AssignProcessToJobObject failed")
    kernel.SetPriorityClass(current, BELOW_NORMAL_PRIORITY_CLASS)
    return kernel, job


def _job_counts(kernel: Any, job: Any) -> tuple[int, int]:
    accounting = BasicAccountingInformation()
    if not kernel.QueryInformationJobObject(
        job, JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION, ctypes.byref(accounting),
        ctypes.sizeof(accounting), None,
    ):
        raise _winerror("QueryInformationJobObject accounting failed")
    extended = ExtendedLimitInformation()
    if not kernel.QueryInformationJobObject(
        job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(extended),
        ctypes.sizeof(extended), None,
    ):
        raise _winerror("QueryInformationJobObject memory failed")
    return int(accounting.ActiveProcesses), int(extended.PeakJobMemoryUsed)


def _terminate_campaign_tree(kernel: Any, job: Any, child: subprocess.Popen[bytes]) -> None:
    if child.poll() is None:
        subprocess.run(
            ["taskkill.exe", "/PID", str(child.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15, check=False, creationflags=CREATE_NO_WINDOW,
        )
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    try:
        active, _ = _job_counts(kernel, job)
    except OSError:
        active = 0
    if active > 1 and not kernel.TerminateJobObject(job, 124):
        raise _winerror("TerminateJobObject campaign tree failed")
