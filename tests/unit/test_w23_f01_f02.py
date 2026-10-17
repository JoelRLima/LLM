"""Focal, non-lifecycle coverage for W23 F01 and F02."""
from __future__ import annotations

import ctypes
import os
import re
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from distribution import candidate_snapshot as snapshot

ROOT = Path(__file__).resolve().parents[2]
USER = b"CURRENT-USER-SID"
OWNER = b"TOKEN-OWNER-GROUP"


class SidAPI:
    """SIMULATED SID memory; no native ACL validation is claimed."""

    def __init__(self) -> None:
        self.buffers: list[Any] = []

    def ConvertStringSidToSidW(self, value: str, pointer: Any) -> int:
        buffer = ctypes.create_string_buffer(value.encode())
        self.buffers.append(buffer)
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.cast(buffer, ctypes.c_void_p)
        return 1

    def GetLengthSid(self, pointer: Any) -> int:
        return len(ctypes.string_at(pointer))


class Kernel:
    def LocalFree(self, _pointer: Any) -> int:
        return 0


@pytest.fixture
def identity_policy(monkeypatch: pytest.MonkeyPatch) -> tuple[set[bytes], SidAPI]:
    monkeypatch.setattr(snapshot, "_windows_current_owner_sid", lambda *_: OWNER)
    monkeypatch.setattr(snapshot, "_windows_current_user_sid", lambda *_: USER, raising=False)
    api = SidAPI()
    return snapshot._windows_trusted_sids(api, Kernel()), api


def test_token_user_is_trusted_when_owner_is_a_distinct_group(
    identity_policy: tuple[set[bytes], SidAPI],
) -> None:
    trusted, _ = identity_policy
    assert USER in trusted
    assert OWNER in trusted
    assert trusted == {USER, OWNER, b"S-1-5-18", b"S-1-5-32-544", b"S-1-3-4",
                       b"S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"}


def _ace(mask: int, sid: bytes) -> Any:
    return ctypes.create_string_buffer(struct.pack("<BBHI", 0, 0, 8 + len(sid), mask) + sid)


@pytest.mark.parametrize("mask", [2, 4, 0x40, 0x10000, 0x40000, 0x80000, 0x10000000, 0x40000000])
@pytest.mark.parametrize("sid", [USER, OWNER])
def test_exact_process_identities_can_hold_namespace_rights(
    identity_policy: tuple[set[bytes], SidAPI], mask: int, sid: bytes,
) -> None:
    trusted, api = identity_policy
    buffer = _ace(mask, sid)
    snapshot._windows_validate_acl_ace(ctypes.cast(buffer, ctypes.c_void_p), trusted, api)


@pytest.mark.parametrize("mask", [2, 4, 0x40, 0x10000, 0x40000, 0x80000, 0x10000000, 0x40000000])
@pytest.mark.parametrize("sid", [b"OTHER-USER-SID", b"OTHER-GROUP-SID"])
def test_other_users_and_groups_remain_untrusted(
    identity_policy: tuple[set[bytes], SidAPI], mask: int, sid: bytes,
) -> None:
    trusted, api = identity_policy
    buffer = _ace(mask, sid)
    with pytest.raises(snapshot.CandidateSnapshotError, match="untrusted namespace"):
        snapshot._windows_validate_acl_ace(ctypes.cast(buffer, ctypes.c_void_p), trusted, api)


def test_unreadable_token_user_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(snapshot, "_windows_current_owner_sid", lambda *_: OWNER)

    def unavailable(*_args: Any) -> bytes:
        raise snapshot.CandidateSnapshotError("cannot inspect the Windows process user")

    monkeypatch.setattr(snapshot, "_windows_current_user_sid", unavailable, raising=False)
    with pytest.raises(snapshot.CandidateSnapshotError, match="process user"):
        snapshot._windows_trusted_sids(SidAPI(), Kernel())


@pytest.mark.skipif(os.name != "nt", reason="native Windows token identity query")
def test_native_token_user_matches_independent_whoami() -> None:
    from ctypes import wintypes

    kernel, api = snapshot._windows_api()
    user = snapshot._windows_current_user_sid(api, kernel)
    pointer = wintypes.LPWSTR()
    api.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    api.ConvertSidToStringSidW.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(user)
    assert api.ConvertSidToStringSidW(ctypes.cast(buffer, ctypes.c_void_p), ctypes.byref(pointer))
    try:
        result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True,
                                text=True, errors="replace", timeout=10, check=True)
        assert pointer.value in re.findall(r"S-1-\d+(?:-\d+)+", result.stdout)
    finally:
        snapshot._windows_local_free(kernel, ctypes.cast(pointer, ctypes.c_void_p))
    assert user in snapshot._windows_trusted_sids(api, kernel)


@pytest.mark.parametrize("entry", ["script", "module"])
@pytest.mark.parametrize("argument", ["--help", "--invalid-w23-targeted-switch"])
def test_w18_verifier_entrypoint_imports_and_preserves_argument_validation(
    tmp_path: Path, entry: str, argument: str,
) -> None:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if entry == "script":
        command = [sys.executable, "-I", str(ROOT / "scripts/verify_installed_product.py"), argument]
        cwd = tmp_path
    else:
        command = [sys.executable, "-B", "-m", "scripts.verify_installed_product", argument]
        cwd = ROOT
    result = subprocess.run(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == (0 if argument == "--help" else 2), result.stdout + result.stderr
    assert "--bundle-dir" in result.stdout + result.stderr
    assert "ModuleNotFoundError" not in result.stderr
    assert "W18_INSTALLED_PRODUCT=PASS" not in result.stdout


@pytest.mark.parametrize("information_class", [1, 4])
@pytest.mark.parametrize("failure", ["none", "open", "size", "read", "null", "length"])
def test_token_sid_query_copies_memory_and_closes_its_handle(
    monkeypatch: pytest.MonkeyPatch, information_class: int, failure: str,
) -> None:
    """SIMULATED WinAPI failures and large handle values, not native token mutation."""
    from ctypes import wintypes

    closed: list[int] = []
    expected_handle = 0x100000001 if ctypes.sizeof(ctypes.c_void_p) == 8 else 123
    sid_bytes = b"EXACT-PROCESS-SID"
    header = 2 * ctypes.sizeof(ctypes.c_void_p) if information_class == 1 else ctypes.sizeof(ctypes.c_void_p)

    class TokenAPI:
        def OpenProcessToken(self, _process: Any, access: int, output: Any) -> bool:
            assert access == 8
            ctypes.cast(output, ctypes.POINTER(wintypes.HANDLE))[0] = wintypes.HANDLE(expected_handle)
            return failure != "open"

        def GetTokenInformation(self, token: Any, kind: int, buffer: Any, _size: int, output: Any) -> bool:
            assert token.value == expected_handle and kind == information_class
            ctypes.cast(output, ctypes.POINTER(wintypes.DWORD))[0] = 0 if failure == "size" else header + len(sid_bytes)
            if buffer is None or failure == "read":
                return False
            address = buffer.value
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0] = None if failure == "null" else address + header
            ctypes.memmove(address + header, sid_bytes, len(sid_bytes))
            return True

        def GetLengthSid(self, pointer: Any) -> int:
            assert ctypes.string_at(pointer, len(sid_bytes)) == sid_bytes
            return 0 if failure == "length" else len(sid_bytes)

    class TokenKernel:
        def GetCurrentProcess(self) -> int:
            return -1

    monkeypatch.setattr(snapshot, "_windows_close_handle", lambda _kernel, handle: closed.append(handle.value))
    if failure == "none":
        copied = snapshot._windows_token_identity_sid(TokenAPI(), TokenKernel(), information_class)
        assert copied == sid_bytes
    else:
        with pytest.raises(snapshot.CandidateSnapshotError, match="cannot inspect the Windows process"):
            snapshot._windows_token_identity_sid(TokenAPI(), TokenKernel(), information_class)
    assert closed == ([] if failure == "open" else [expected_handle])


@pytest.mark.parametrize("replacement", [None, "scripts/unrelated.py", "scripts.verify_installed_product"])
def test_w18_checker_keeps_rejecting_unapproved_verifier_wiring(tmp_path: Path, replacement: str | None) -> None:
    from scripts import check_wave18_architecture as checker

    workflows = tmp_path / ".github/workflows"
    workflows.mkdir(parents=True)
    for name in ["ci.yml", "wave18-installed-product.yml"]:
        content = (ROOT / ".github/workflows" / name).read_bytes()
        if replacement is not None and name == "wave18-installed-product.yml":
            content = content.replace(b"scripts/verify_installed_product.py", replacement.encode())
        (workflows / name).write_bytes(content)
    findings = checker._v3_workflow_findings(tmp_path)
    if replacement is None:
        assert findings == []
    else:
        assert any(row.rule_id == "W18-V3-12" and "verify_installed_product.py" in row.detail for row in findings)


@pytest.mark.parametrize("information_class", [0, 2, 3, 5])
def test_non_identity_token_information_is_rejected(information_class: int) -> None:
    with pytest.raises(snapshot.CandidateSnapshotError, match="unsupported Windows token identity"):
        snapshot._windows_token_identity_sid(None, None, information_class)
