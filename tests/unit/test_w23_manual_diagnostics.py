"""Owner-run tests for the manual diagnostics. Not executed during implementation."""
from __future__ import annotations

import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any

import pytest

from scripts.w23_diagnostics import export, inputs, native, phases, profile
from scripts.w23_diagnostics.capture import LIMIT, Capture
from scripts.w23_diagnostics.evidence import Redactor, digest, write_json
from scripts.w23_diagnostics.process import Commands, environment

ROOT = Path(__file__).resolve().parents[2]
NATIVE = (os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true"
          and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted")


def test_incomplete_or_unsuccessful_step_never_becomes_pass() -> None:
    assert export.terminal_summary({"status": "PASS", "completed": False}, "success")["status"] == "UNKNOWN"
    assert export.terminal_summary({"status": "PASS", "completed": True}, "failure")["status"] == "UNKNOWN"
    assert export.terminal_summary({"status": "PASS", "completed": True}, "success")["status"] == "PASS"


@pytest.mark.parametrize("state", ["FAIL", "TIMEOUT", "UNKNOWN"])
def test_terminal_failure_is_preserved(state: str) -> None:
    assert export.terminal_summary({"status": state, "completed": True}, "failure")["status"] == state


def test_child_environment_does_not_inherit_tokens(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-must-not-be-inherited")
    monkeypatch.setenv("ACTIONS_RUNTIME_TOKEN", "fake-artifact-token-must-not-be-inherited")
    monkeypatch.setenv("PIP_INDEX_URL", "https://user:fake-password@example.invalid/simple")
    env = environment(tmp_path / "temp", ROOT)
    assert "GITHUB_TOKEN" not in env
    assert "ACTIONS_RUNTIME_TOKEN" not in env
    assert "PIP_INDEX_URL" not in env
    assert Path(env["TEMP"]).is_relative_to(tmp_path)


def test_capture_redacts_secret_split_across_pipe_reads(tmp_path: Path, monkeypatch: Any) -> None:
    secret = "fake-diagnostic-secret-value"
    monkeypatch.setenv("W23_TEST_SECRET", secret)
    reader, writer = os.pipe()
    capture = Capture(reader, tmp_path / "capture.log", Redactor())
    capture.thread.start()
    try:
        os.write(writer, b"value=fake-diagnostic-")
        os.write(writer, b"secret-value\n")
    finally:
        os.close(writer)
    capture.thread.join(timeout=5)
    assert not capture.thread.is_alive()
    assert secret in capture.text()  # Verifier semantics use bounded memory, never exported raw.
    public = (tmp_path / "capture.log").read_text()
    assert secret not in public
    assert "[REDACTED]" in public


def test_capture_caps_disk_and_raw_memory(tmp_path: Path) -> None:
    reader, writer = os.pipe()
    capture = Capture(reader, tmp_path / "capture.log", Redactor())
    capture.thread.start()
    try:
        for _ in range(768):
            os.write(writer, b"x" * 4095 + b"\n")
    finally:
        os.close(writer)
    capture.thread.join(timeout=5)
    assert not capture.thread.is_alive()
    assert capture.limited
    assert len(capture.raw) <= LIMIT
    assert (tmp_path / "capture.log").stat().st_size <= LIMIT


def test_authentication_rejects_changed_bytes(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "source"
    source.mkdir()
    data = source / "module.py"
    data.write_text("pass\n")
    monkeypatch.setattr(inputs, "contract", lambda: {"files": {"module.py": digest(data)}, "patched_files": {}})
    inputs.authenticate(source, tmp_path / "before.json")
    expected = digest(data)
    monkeypatch.setattr(inputs, "contract", lambda: {"files": {"module.py": expected}, "patched_files": {}})
    data.write_text("changed = True\n")
    with pytest.raises(phases.StageError):
        inputs.authenticate(source, tmp_path / "after.json")


def test_focal_junit_cannot_hide_a_skip(tmp_path: Path) -> None:
    xml = tmp_path / "result.xml"
    xml.write_text('<testsuite><testcase name="test_current_checker_enforces_the_complete_repository_graph"><skipped/></testcase></testsuite>')
    assert phases.junit(xml, 1)["status"] == "FAIL"


def test_focal_junit_requires_the_exact_named_test(tmp_path: Path) -> None:
    xml = tmp_path / "result.xml"
    xml.write_text('<testsuite><testcase name="different_test"/></testsuite>')
    assert phases.junit(xml, 1)["status"] == "FAIL"


def test_wrong_working_directory_is_not_silently_accepted(tmp_path: Path) -> None:
    # The real profiler is a standalone child; this pytest process already loaded scripts.
    with pytest.raises(RuntimeError, match="already loaded"):
        profile.load_checker(tmp_path)


@pytest.mark.parametrize("different", [False, True])
def test_r02_semantics_comparison_is_fail_closed(tmp_path: Path, monkeypatch: Any, different: bool) -> None:
    monkeypatch.setattr(phases, "reconstruct", lambda work, name, commands, **kwargs: work / name)
    monkeypatch.setattr(phases, "dependencies", lambda *args: None)
    monkeypatch.setattr(phases, "authenticate", lambda *args, **kwargs: {})
    monkeypatch.setattr(phases, "focal", lambda *args, **kwargs: {"status": "PASS"})
    def fake_profile(work: Path, source: Path, name: str, commands: Any) -> dict[str, Any]:
        return {"status": "PASS", "errors": [], "counts": {"modules": 2 if different and name == "patched" else 1}}
    monkeypatch.setattr(phases, "profile", fake_profile)
    result = phases.r02(tmp_path, None)  # type: ignore[arg-type]
    assert result["status"] == ("FAIL" if different else "PASS")
    assert result["global_suite_timeout_resolved"] == "UNKNOWN"


def test_export_does_not_promote_missing_results(tmp_path: Path) -> None:
    (tmp_path / "publish").mkdir()
    assert export.export(tmp_path, "failure") == 1
    summary = json.loads((tmp_path / "upload/summary.json").read_text())
    assert summary["status"] == "UNKNOWN"
    assert summary["w23"] == "BLOCKED"


def test_export_manifest_authenticates_only_public_files(tmp_path: Path) -> None:
    (tmp_path / "publish").mkdir()
    (tmp_path / "private").mkdir()
    (tmp_path / "private/do-not-upload.raw").write_text("private fixture")
    write_json(tmp_path / "publish/summary.json", {"status": "PASS", "completed": True})
    assert export.export(tmp_path, "success") == 0
    manifest = json.loads((tmp_path / "upload/SHA256.json").read_text())
    assert all(".raw" not in path for path in manifest)
    assert manifest["summary.json"] == digest(tmp_path / "upload/summary.json")


def test_large_handles_are_not_narrowed_or_resumed_after_assignment_failure(monkeypatch: Any) -> None:
    calls: list[str] = []
    class Kernel:
        def AssignProcessToJobObject(self, job: Any, handle: Any) -> bool:
            assert handle == 0x12345678ABCDEF01
            calls.append("assign")
            return False
        def ResumeThread(self, thread: Any) -> int:
            calls.append("resume")
            return 1
    child = object.__new__(native.NativeChild)
    child.process, child.thread = 0x12345678ABCDEF01, 0x12345678ABCDEF02
    child.job = type("Job", (), {"kernel": Kernel(), "handle": 0x12345678ABCDEF03})()
    monkeypatch.setattr(native, "_winerror", lambda message: OSError(message))
    with pytest.raises(OSError):
        child._contain_and_resume()
    assert calls == ["assign"]


@pytest.mark.skipif(not NATIVE, reason="native containment only on disposable GitHub-hosted Windows")
def test_native_win64_handle_signatures() -> None:
    kernel = native.kernel_api()
    assert ctypes.sizeof(wintypes.HANDLE) == 8
    assert kernel.CloseHandle.argtypes == [wintypes.HANDLE]
    assert kernel.ResumeThread.argtypes == [wintypes.HANDLE]


@pytest.mark.skipif(not NATIVE, reason="never spawn diagnostic test children on development host")
def test_native_owned_process_and_timeout(tmp_path: Path) -> None:
    commands = Commands(tmp_path / "evidence", Redactor([tmp_path]))
    env = environment(tmp_path / "temp", ROOT)
    passed = commands.run("smoke", [sys.executable, "-u", "-c", "print('owned-process-marker')"], ROOT, env, 10)
    assert passed.status == "PASS"
    assert "owned-process-marker" in passed.stdout
    timed = commands.run("timeout", [sys.executable, "-u", "-c", "import time; time.sleep(120)"], ROOT, env, 1)
    assert timed.status == "TIMEOUT"
