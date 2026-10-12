from __future__ import annotations

import ctypes
import os
from types import SimpleNamespace

import pytest

from llm_agent.process import process_identity
from llm_agent.process.process_identity import OwnerStatus, ProcessOwnerLiveness


def _proc_stat(state: str) -> str:
    fields = [state] + [str(index) for index in range(1, 20)]
    return f"2176 (worker with ) name) {' '.join(fields)}\n"


def test_linux_proc_parser_preserves_boot_and_start_identity() -> None:
    status, identity = process_identity._parse_linux_process_stat(
        _proc_stat("R"),
        "boot-identity",
    )

    assert status is OwnerStatus.ALIVE
    assert identity == "linux:boot-identity:19"


@pytest.mark.parametrize("state", ("Z", "X", "x"))
def test_linux_proc_parser_classifies_terminal_states_as_dead(state: str) -> None:
    status, identity = process_identity._parse_linux_process_stat(
        _proc_stat(state),
        "",
    )

    assert status is OwnerStatus.DEAD
    assert identity is None


def test_linux_proc_parser_keeps_unknown_state_indeterminate() -> None:
    status, identity = process_identity._parse_linux_process_stat(
        _proc_stat("?"),
        "boot-identity",
    )

    assert status is OwnerStatus.INDETERMINATE
    assert identity is None


def test_linux_proc_parser_keeps_malformed_identity_indeterminate() -> None:
    fields = ["S"] + [str(index) for index in range(1, 19)] + ["not-a-tick"]
    status, identity = process_identity._parse_linux_process_stat(
        f"2176 (worker) {' '.join(fields)}\n",
        "boot-identity",
    )

    assert status is OwnerStatus.INDETERMINATE
    assert identity is None


@pytest.mark.parametrize(
    ("snapshot", "expected", "result"),
    (
        ((OwnerStatus.DEAD, None), "linux:old", OwnerStatus.DEAD),
        ((OwnerStatus.INDETERMINATE, None), "linux:old", OwnerStatus.INDETERMINATE),
        ((OwnerStatus.ALIVE, "linux:current"), "linux:old", OwnerStatus.DEAD),
        ((OwnerStatus.ALIVE, "linux:current"), None, OwnerStatus.ALIVE),
    ),
)
def test_posix_liveness_uses_terminal_state_and_identity_safely(
    monkeypatch: pytest.MonkeyPatch,
    snapshot: tuple[OwnerStatus, str | None],
    expected: str | None,
    result: OwnerStatus,
) -> None:
    monkeypatch.setattr(process_identity.os, "kill", lambda pid, signal: None)
    monkeypatch.setattr(process_identity.Path, "is_dir", lambda self: True)
    monkeypatch.setattr(process_identity, "_linux_process_snapshot", lambda pid: snapshot)

    assert ProcessOwnerLiveness._check_posix(os.getpid(), expected) is result


def test_posix_permission_failure_is_indeterminate(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(pid: int, signal: int) -> None:
        raise PermissionError(pid, signal)

    monkeypatch.setattr(process_identity.os, "kill", denied)

    assert ProcessOwnerLiveness._check_posix(2176, None) is OwnerStatus.INDETERMINATE


def test_posix_missing_identity_support_is_indeterminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_identity.os, "kill", lambda pid, signal: None)
    monkeypatch.setattr(process_identity.Path, "is_dir", lambda self: False)

    assert ProcessOwnerLiveness._check_posix(2176, "linux:boot:tick") is OwnerStatus.INDETERMINATE


@pytest.mark.skipif(os.name != "nt", reason="Windows kernel32 snapshot contract")
@pytest.mark.parametrize(
    ("times_succeed", "expected_status", "expected_identity"),
    (
        (True, OwnerStatus.ALIVE, "windows:55834574859"),
        (False, OwnerStatus.INDETERMINATE, None),
    ),
)
def test_windows_snapshot_closes_open_handle(
    monkeypatch: pytest.MonkeyPatch,
    times_succeed: bool,
    expected_status: OwnerStatus,
    expected_identity: str | None,
) -> None:
    closed: list[int] = []

    def open_process(access: int, inherit: bool, pid: int) -> int:
        assert (access, inherit, pid) == (0x1000, False, 2176)
        return 41

    def exit_code(handle: int, value: object) -> int:
        ctypes.cast(value, ctypes.POINTER(ctypes.c_ulong)).contents.value = 259
        return 1

    def process_times(
        handle: int,
        creation: object,
        exit_time: object,
        kernel_time: object,
        user_time: object,
    ) -> int:
        del handle, exit_time, kernel_time, user_time
        fields = ctypes.cast(creation, ctypes.POINTER(ctypes.c_uint32))
        fields[0] = 11
        fields[1] = 13
        return int(times_succeed)

    kernel32 = SimpleNamespace(
        OpenProcess=open_process,
        GetExitCodeProcess=exit_code,
        GetProcessTimes=process_times,
        CloseHandle=lambda handle: closed.append(handle) or 1,
    )
    for function in vars(kernel32).values():
        function.argtypes = None
        function.restype = None

    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error: kernel32)

    assert process_identity._windows_process_snapshot(2176) == (
        expected_status,
        expected_identity,
    )
    assert closed == [41]
