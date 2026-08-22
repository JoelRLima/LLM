from __future__ import annotations

import os

import pytest

from agent.runtime import process_identity
from agent.runtime.process_identity import OwnerStatus, ProcessOwnerLiveness


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
