"""Windows shared lifetime reference for an installed candidate.

Each runtime briefly owns a per-candidate gate, then retains an unowned handle
to a named active object.  Many processes may retain that handle concurrently;
Windows destroys the object after the last handle closes, including on crash.
"""

from __future__ import annotations

import atexit
import ctypes
import os
import re
from pathlib import Path
from typing import Any, cast

CANDIDATE_ID_PATTERN = re.compile(r"^w18-[0-9a-f]{32}$")
SID_PATTERN = re.compile(r"^S-[0-9]+(?:-[0-9]+)+$")
GATE_PREFIX = r"Global\W18-candidate-gate-"
ACTIVE_PREFIX = r"Global\W18-candidate-active-"

_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF
_INFINITE = 0xFFFFFFFF
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1


def _win_dll(name: str) -> Any:
    """Load a Win32 DLL without exposing platform-specific ctypes stubs."""

    return ctypes.__dict__["WinDLL"](name, use_last_error=True)


def _last_error() -> int:
    return int(ctypes.__dict__["get_last_error"]())


def _win_error(code: int | None = None) -> OSError:
    factory = ctypes.__dict__["WinError"]
    return cast(OSError, factory(_last_error() if code is None else code))


class CandidateLeaseError(RuntimeError):
    """Base error for candidate lease acquisition failures."""


def is_valid_candidate_id(candidate_id: object) -> bool:
    """Validate the canonical W18 candidate directory grammar."""

    return isinstance(candidate_id, str) and CANDIDATE_ID_PATTERN.fullmatch(candidate_id) is not None


def current_user_sid() -> str:
    """Return the current token SID using Windows token APIs."""

    if os.name != "nt":
        raise CandidateLeaseError("Windows SID lookup is unavailable on this platform")

    advapi32 = _win_dll("advapi32")
    kernel32 = _win_dll("kernel32")
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.restype = ctypes.c_void_p
    open_process_token = advapi32.OpenProcessToken
    open_process_token.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    open_process_token.restype = ctypes.c_int
    get_token_information = advapi32.GetTokenInformation
    get_token_information.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
    get_token_information.restype = ctypes.c_int
    convert_sid = advapi32.ConvertSidToStringSidW
    convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    convert_sid.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    token = ctypes.c_void_p()
    if not open_process_token(get_current_process(), _TOKEN_QUERY, ctypes.byref(token)):
        raise _win_error()
    try:
        required = ctypes.c_uint32()
        get_token_information(token, _TOKEN_USER, None, 0, ctypes.byref(required))
        if not required.value:
            raise _win_error()
        buffer = ctypes.create_string_buffer(required.value)
        if not get_token_information(token, _TOKEN_USER, buffer, required.value, ctypes.byref(required)):
            raise _win_error()
        # TOKEN_USER starts with a SID_AND_ATTRIBUTES whose first field is the
        # PSID pointer on both 32-bit and 64-bit Windows.
        sid_pointer = ctypes.c_void_p.from_buffer(buffer).value
        if not sid_pointer:
            raise CandidateLeaseError("current token has no user SID")
        rendered = ctypes.c_wchar_p()
        if not convert_sid(ctypes.c_void_p(sid_pointer), ctypes.byref(rendered)):
            raise _win_error()
        try:
            sid = rendered.value or ""
        finally:
            local_free(rendered)
        if not sid:
            raise CandidateLeaseError("current token SID is empty")
        return sid
    finally:
        close_handle(token)


def _candidate_object_name(prefix: str, candidate_id: str, sid: str | None) -> str:

    if not is_valid_candidate_id(candidate_id):
        raise ValueError("candidate_id is not a canonical W18 candidate ID")
    selected_sid = current_user_sid() if sid is None else sid
    if not isinstance(selected_sid, str) or SID_PATTERN.fullmatch(selected_sid) is None:
        raise ValueError("Windows user SID is invalid")
    return f"{prefix}{selected_sid}-{candidate_id}"


def candidate_gate_name(candidate_id: str, sid: str | None = None) -> str:
    """Build the serialized startup/deletion gate name."""

    return _candidate_object_name(GATE_PREFIX, candidate_id, sid)


def candidate_active_name(candidate_id: str, sid: str | None = None) -> str:
    """Build the shared active-reference object name."""

    return _candidate_object_name(ACTIVE_PREFIX, candidate_id, sid)


def interpret_mutex_wait(result: int) -> str:
    """Classify the non-blocking Win32 wait, including abandoned ownership."""

    if result in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
        return "acquired"
    if result == _WAIT_TIMEOUT:
        return "active"
    return "ambiguous"


class CandidateLease:
    """A retained, unowned active-object handle released on process exit."""

    def __init__(self, handle: int, name: str) -> None:
        self.handle = ctypes.c_void_p(handle)
        self.name = name
        self._released = False

    def release(self) -> None:
        if self._released or not self.handle:
            return
        kernel32 = _win_dll("kernel32")
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        close_handle(self.handle)
        self._released = True
        self.handle = ctypes.c_void_p()

    def __enter__(self) -> "CandidateLease":
        return self

    def __exit__(self, *_: Any) -> None:
        self.release()


def acquire_candidate_lease(candidate_id: str, *, sid: str | None = None) -> CandidateLease | None:
    """Create a shared active reference while serialized by the gate."""

    if os.name != "nt":
        return None
    gate_name = candidate_gate_name(candidate_id, sid)
    active_name = candidate_active_name(candidate_id, sid)
    kernel32 = _win_dll("kernel32")
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    create_mutex.restype = ctypes.c_void_p
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    wait_for_single_object.restype = ctypes.c_uint32
    release_mutex = kernel32.ReleaseMutex
    release_mutex.argtypes = [ctypes.c_void_p]
    release_mutex.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    gate = create_mutex(None, 0, gate_name)
    if not gate:
        raise _win_error()
    result = wait_for_single_object(gate, _INFINITE)
    if interpret_mutex_wait(result) != "acquired":
        error = _last_error() if result == _WAIT_FAILED else 0
        close_handle(gate)
        raise _win_error(error) if error else CandidateLeaseError("candidate gate acquisition failed")
    try:
        active = create_mutex(None, 0, active_name)
        if not active:
            raise _win_error()
        return CandidateLease(int(active), active_name)
    finally:
        release_mutex(gate)
        close_handle(gate)


def candidate_id_from_launcher(launcher: str | os.PathLike[str]) -> str | None:
    """Derive an installed candidate ID; raw relocated payloads return None."""

    path = Path(launcher).resolve()
    if path.name.casefold() != "launcher.py" or path.parent.name.casefold() != "app":
        return None
    candidate = path.parent.parent
    if candidate.parent.name.casefold() != "versions":
        return None
    value = candidate.name
    return value if is_valid_candidate_id(value) else None


_runtime_lease: CandidateLease | None = None


def acquire_runtime_candidate_lease(launcher: str | os.PathLike[str]) -> CandidateLease | None:
    """Acquire the lease at the installed launcher bootstrap boundary."""

    global _runtime_lease
    if os.name != "nt" or _runtime_lease is not None:
        return _runtime_lease
    candidate_id = candidate_id_from_launcher(launcher)
    if candidate_id is None:
        return None
    _runtime_lease = acquire_candidate_lease(candidate_id)
    return _runtime_lease


def _release_runtime_lease() -> None:
    global _runtime_lease
    if _runtime_lease is not None:
        _runtime_lease.release()
        _runtime_lease = None


atexit.register(_release_runtime_lease)

# Descriptive aliases keep the small protocol easy to consume from focused
# tests without creating a second identity implementation.
candidate_mutex_name = candidate_active_name
build_candidate_mutex_name = candidate_active_name
get_current_user_sid = current_user_sid
validate_candidate_id = is_valid_candidate_id


__all__ = [
    "CANDIDATE_ID_PATTERN",
    "CandidateLease",
    "CandidateLeaseError",
    "acquire_candidate_lease",
    "acquire_runtime_candidate_lease",
    "candidate_active_name",
    "candidate_gate_name",
    "candidate_id_from_launcher",
    "candidate_mutex_name",
    "build_candidate_mutex_name",
    "current_user_sid",
    "get_current_user_sid",
    "interpret_mutex_wait",
    "is_valid_candidate_id",
    "validate_candidate_id",
]
