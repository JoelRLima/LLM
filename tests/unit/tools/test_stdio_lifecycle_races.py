"""Regression coverage for EOF drain, early exit and primary failure ordering."""

import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from agent.tools import stdio_process as stdio
from agent.tools.contracts import ToolStatus


@pytest.fixture(autouse=True)
def no_surviving_readers() -> Any:
    yield
    assert not [t.name for t in threading.enumerate() if t.name.startswith("stdio-")]


@pytest.mark.parametrize("stream", ["stdout", "stderr", "both"])
@pytest.mark.parametrize("size", [1024, 1025, 4096])
def test_immediate_exit_drains_stream_boundaries(stream: str, size: int) -> None:
    source = "import sys, threading\n"
    source += "def emit(s):\n s.write(b'x' * " + str(size) + "); s.flush()\n"
    streams = ["stdout", "stderr"] if stream == "both" else [stream]
    source += "threads = []\n"
    for name in streams:
        source += f"t = threading.Thread(target=emit, args=(sys.{name}.buffer,)); t.start(); threads.append(t)\n"
    source += "for t in threads: t.join()\n"
    outcome = stdio.run_stdio_process(
        entrypoint=(sys.executable, "-c", source), cwd=None, timeout_seconds=5,
        payload={}, stdout_limit=1024, stderr_limit=1024,
    )
    if size > 1024:
        assert outcome.failure is not None
        assert outcome.failure.status == ToolStatus.PROTOCOL_ERROR
        assert outcome.failure.code == ("STDERR_OUTPUT_LIMIT" if stream == "stderr" else "OUTPUT_LIMIT")
    else:
        assert outcome.failure is None
        assert outcome.completed is not None
        assert outcome.completed.stdout == ("x" * size if "stdout" in streams else "")
        assert outcome.completed.stderr == (b"x" * size if "stderr" in streams else b"")


def test_join_allows_all_pending_chunks_before_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()
    stop = threading.Event()
    capture = stdio._StreamCapture(1024)

    class Stream:
        chunks = iter([b"x" * 1024, b"y", b""])

        def read(self, size: int) -> bytes:
            assert release.wait(1)
            return next(self.chunks)

    reader = threading.Thread(target=stdio._drain_stream, args=(Stream(), capture, stop), name="stdio-test-drain")
    original_join = reader.join

    def join(timeout: float | None = None) -> None:
        release.set()
        original_join(timeout)

    monkeypatch.setattr(reader, "join", join)
    context = stdio._ProcessContext(
        process=Any, windows_job=None, readers=[reader], stdout=capture,
        stderr=stdio._StreamCapture(1024), stop_readers=stop,
    )
    reader.start()
    try:
        assert stdio._join_readers(context) is None
        assert capture.received == 1025
        assert capture.exceeded
        assert not stop.is_set()
    finally:
        release.set()
        original_join(1)


@pytest.mark.parametrize("code,during_monitor", [
    ("OUTPUT_LIMIT", False), ("STDERR_OUTPUT_LIMIT", False),
    ("OUTPUT_LIMIT", True), ("STDERR_OUTPUT_LIMIT", True),
    ("TIMEOUT", True), ("CANCELLED", True),
])
@pytest.mark.parametrize("with_cleanup", [False, True])
def test_failure_precedence_matrix(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    code: str, during_monitor: bool, with_cleanup: bool,
) -> None:
    status = {"TIMEOUT": ToolStatus.TIMED_OUT, "CANCELLED": ToolStatus.CANCELLED}.get(code, ToolStatus.PROTOCOL_ERROR)
    primary = stdio.ProcessFailure(status, code, "primary-" + "x" * 5000, "primary")
    secondary = stdio.ProcessFailure(ToolStatus.UNAVAILABLE, "CLEANUP_ERROR", "cleanup diagnostic", "cleanup")
    process = type("Process", (), {"wait": lambda self: 0})()
    context = stdio._ProcessContext(
        process=process, windows_job=None, readers=[],
        stdout=stdio._StreamCapture(1024), stderr=stdio._StreamCapture(1024), status_path=Path("unused"),
    )
    monkeypatch.setattr(stdio, "_start_process", lambda *a: context)
    monkeypatch.setattr(stdio, "_send_request", lambda *a: None)
    monkeypatch.setattr(stdio, "_monitor_process", lambda *a: primary if during_monitor else None)
    monkeypatch.setattr(stdio, "_limit_failure", lambda *a: primary)
    monkeypatch.setattr(stdio, "launcher_status_failure_for_path", lambda *a: None)
    monkeypatch.setattr(stdio, "_cleanup", lambda *a, **kw: secondary if with_cleanup else None)
    result = stdio.run_stdio_process(
        entrypoint=(sys.executable,), cwd=None, timeout_seconds=1,
        payload={}, stdout_limit=1024, stderr_limit=1024,
    )
    assert result.failure is (secondary if with_cleanup else primary)
    if with_cleanup:
        assert "CLEANUP_ERROR" in caplog.text
        assert code in caplog.text
        assert "primary" in caplog.text
        assert "x" * 2000 not in caplog.text
    else:
        assert not caplog.records


def test_natural_completion_preserves_launcher_status_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stdio.os, "name", "nt")
    primary = stdio.ProcessFailure(
        ToolStatus.PROTOCOL_ERROR,
        "OUTPUT_LIMIT",
        "stream limit",
        "stream limit",
    )
    process = type("Process", (), {"wait": lambda self: 0})()
    context = stdio._ProcessContext(
        process=process,
        windows_job=None,
        readers=[],
        stdout=stdio._StreamCapture(1024),
        stderr=stdio._StreamCapture(1024),
        status_path=Path("unused"),
    )
    monkeypatch.setattr(stdio, "_start_process", lambda *a: context)
    monkeypatch.setattr(stdio, "_send_request", lambda *a: None)
    monkeypatch.setattr(stdio, "_monitor_process", lambda *a: None)
    monkeypatch.setattr(stdio, "_limit_failure", lambda *a: primary)
    monkeypatch.setattr(
        stdio,
        "launcher_status_failure_for_path",
        lambda *a: ("EXTENSION_START_FAILED", "launcher diagnostic"),
    )
    monkeypatch.setattr(stdio, "_cleanup", lambda *a, **kw: None)

    result = stdio.run_stdio_process(
        entrypoint=(sys.executable,),
        cwd=None,
        timeout_seconds=1,
        payload={},
        stdout_limit=1024,
        stderr_limit=1024,
    )

    assert result.failure is not None
    assert result.failure.status == ToolStatus.UNAVAILABLE
    assert result.failure.code == "EXTENSION_START_FAILED"


def test_emergency_join_keeps_existing_total_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    stop = threading.Event()
    waits = []

    class Reader:
        name = "stdio-fake-reader"
        alive = True

        def join(self, timeout: float) -> None:
            waits.append(timeout)
            if stop.is_set():
                self.alive = False
            clock[0] += timeout

        def is_alive(self) -> bool:
            return self.alive

    reader = Reader()
    context = stdio._ProcessContext(
        process=Any, windows_job=None, readers=[reader], stdout=stdio._StreamCapture(1),
        stderr=stdio._StreamCapture(1), stop_readers=stop,
    )
    monkeypatch.setattr(stdio.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(stdio, "_close_pipes", lambda p: None)
    assert stdio._join_readers(context) is None
    assert stop.is_set()
    assert waits == [stdio.CLEANUP_TIMEOUT_SECONDS / 2] * 2


def test_launcher_immediate_exit_with_large_unread_request(tmp_path: Path) -> None:
    from agent.tools import stdio_launcher

    status_path = tmp_path / "early.status"
    assert stdio_launcher._run({
        "command": [sys.executable, "-c", "pass"],
        "request_line": "x" * (1024 * 1024), "status_path": str(status_path),
    }) == 0
    assert stdio_launcher.read_launcher_status(status_path) == {"state": "extension_started"}


def test_launcher_reports_real_startup_error(tmp_path: Path) -> None:
    from agent.tools import stdio_launcher

    status_path = tmp_path / "failed.status"
    assert stdio_launcher._run({
        "command": [str(tmp_path / "missing-executable")],
        "request_line": "{}", "status_path": str(status_path),
    }) == 1
    assert stdio_launcher.read_launcher_status(status_path)["code"] == "EXTENSION_START_FAILED"
