"""Regression coverage for EOF drain, early exit and primary failure ordering."""

import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import llm_agent.execution.cleanup as cleanup
import llm_agent.process.streams as streams
from llm_agent.agent.tools import stdio_process as stdio
from llm_agent.agent.tools.contracts import ToolStatus
from llm_agent.execution import CommandExecutionError, CommandResult


@pytest.fixture(autouse=True)
def no_surviving_readers() -> Any:
    yield
    assert not [t.name for t in threading.enumerate() if t.name.startswith("stdio-")]


def test_stdio_environment_does_not_inherit_model_credential_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "OPENAI_API_KEY"
    monkeypatch.setenv(name, "PV155_SECRET_SENTINEL_8fd77e")

    child_environment = stdio._safe_environment()

    assert name not in child_environment
    assert "PV155_SECRET_SENTINEL_8fd77e" not in repr(child_environment)


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
    capture = streams.StreamCapture(1024)

    class Stream:
        chunks = iter([b"x" * 1024, b"y", b""])

        def read(self, size: int) -> bytes:
            assert release.wait(1)
            return next(self.chunks)

    reader = threading.Thread(target=streams.drain_stream, args=(Stream(), capture, stop), name="stdio-test-drain")
    original_join = reader.join

    def join(timeout: float | None = None) -> None:
        release.set()
        original_join(timeout)

    monkeypatch.setattr(reader, "join", join)
    context = cleanup.CommandContext(
        process=Any, windows_job=None, process_group=None, readers=[reader], stdout=capture,
        stderr=streams.StreamCapture(1024), stop_readers=stop, reader_errors=[],
    )
    reader.start()
    try:
        assert cleanup._join_readers(context) is None
        assert capture.received == 1025
        assert capture.exceeded
        assert not stop.is_set()
    finally:
        release.set()
        original_join(1)


@pytest.mark.parametrize(
    "code,during_monitor",
    [
        ("OUTPUT_LIMIT", False),
        ("STDERR_OUTPUT_LIMIT", False),
        ("OUTPUT_LIMIT", True),
        ("STDERR_OUTPUT_LIMIT", True),
        ("TIMEOUT", True),
        ("CANCELLED", True),
    ],
)
@pytest.mark.parametrize("with_cleanup", [False, True])
def test_failure_precedence_matrix(
    monkeypatch: pytest.MonkeyPatch,
    code: str, during_monitor: bool, with_cleanup: bool,
) -> None:
    result = CommandResult(
        return_code=0,
        stdout=b"",
        stderr=b"",
        duration_seconds=0.0,
        cancelled=code == "CANCELLED",
        timed_out=code == "TIMEOUT",
        stdout_truncated=code == "OUTPUT_LIMIT",
        stderr_truncated=code == "STDERR_OUTPUT_LIMIT",
    )

    class FakeExecutor:
        def execute(self, request: object) -> CommandResult:
            del request
            if with_cleanup:
                raise CommandExecutionError(
                    f"primary-{code}-{during_monitor}; cleanup diagnostic",
                    code="CLEANUP_ERROR",
                    detail=f"primary-{code}-{during_monitor}; cleanup diagnostic",
                )
            return result

    monkeypatch.setattr(stdio, "CommandExecutor", FakeExecutor)
    real_os = stdio.os
    monkeypatch.setattr(stdio, "os", SimpleNamespace(name="posix", environ=real_os.environ))
    monkeypatch.setattr(stdio, "prepare_launcher", lambda entrypoint: (entrypoint, None))
    result = stdio.run_stdio_process(
        entrypoint=(sys.executable,), cwd=None, timeout_seconds=1,
        payload={}, stdout_limit=1024, stderr_limit=1024,
    )
    assert result.failure is not None
    if with_cleanup:
        assert result.failure.code == "CLEANUP_ERROR"
        assert code in result.failure.detail
        assert "cleanup diagnostic" in result.failure.detail
    else:
        assert result.failure.code == code
        assert result.failure.status in {
            ToolStatus.PROTOCOL_ERROR,
            ToolStatus.TIMED_OUT,
            ToolStatus.CANCELLED,
        }
    assert len(result.failure.detail) < 1024


def test_natural_completion_preserves_launcher_status_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_os = stdio.os
    monkeypatch.setattr(stdio, "os", SimpleNamespace(name="nt", environ=real_os.environ))
    monkeypatch.setattr(stdio, "prepare_launcher", lambda entrypoint: (entrypoint, Path("unused")))
    monkeypatch.setattr(
        stdio,
        "CommandExecutor",
        lambda: type(
            "FakeExecutor",
            (),
            {
                "execute": lambda self, request: CommandResult(
                    0, b"", b"", 0.0, stdout_truncated=True
                )
            },
        )(),
    )
    monkeypatch.setattr(
        stdio,
        "launcher_status_error",
        lambda *a: ("EXTENSION_START_FAILED", "launcher diagnostic"),
    )

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
    context = cleanup.CommandContext(
        process=Any, windows_job=None, process_group=None, readers=[reader], stdout=streams.StreamCapture(1),
        stderr=streams.StreamCapture(1), stop_readers=stop, reader_errors=[],
    )
    monkeypatch.setattr(cleanup.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cleanup, "close_pipes", lambda p: ())
    assert cleanup._join_readers(context) is None
    assert stop.is_set()
    assert waits == [0.5, 0.5]


def test_launcher_immediate_exit_with_large_unread_request(tmp_path: Path) -> None:
    from llm_agent.extensions import stdio_launcher

    status_path = tmp_path / "early.status"
    assert stdio_launcher._run({
        "command": [sys.executable, "-c", "pass"],
        "request_line": "x" * (1024 * 1024), "status_path": str(status_path),
    }) == 0
    assert stdio_launcher.read_launcher_status(status_path) == {"state": "extension_started"}


def test_launcher_reports_real_startup_error(tmp_path: Path) -> None:
    from llm_agent.extensions import stdio_launcher

    status_path = tmp_path / "failed.status"
    assert stdio_launcher._run({
        "command": [str(tmp_path / "missing-executable")],
        "request_line": "{}", "status_path": str(status_path),
    }) == 1
    assert stdio_launcher.read_launcher_status(status_path)["code"] == "EXTENSION_START_FAILED"
