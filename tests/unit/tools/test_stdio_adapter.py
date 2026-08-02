import json
import os
import select
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

import agent.tools.stdio_adapter as stdio_adapter_module
import agent.tools.stdio_cleanup as stdio_cleanup_module
import agent.tools.stdio_launcher as stdio_launcher_module
import agent.tools.stdio_process as stdio_process_module
from agent.tools.contracts import ToolInvocation, ToolStatus
from agent.tools.stdio_adapter import ExtensionManifest, StdioToolAdapter, load_extension_manifest


def _wait_for_path(path: Path, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not path.exists():
        time.sleep(0.01)
    return path.exists()


_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF
_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000


def _read_test_pid(pid_path: Path) -> int:
    pid = int(pid_path.read_text(encoding="utf-8"))
    assert pid > 0
    return pid


def _windows_process_api() -> Any:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _wait_for_windows_handle(kernel32: Any, handle: Any, timeout_ms: int) -> int:
    result = int(kernel32.WaitForSingleObject(handle, timeout_ms))
    if result == _WAIT_FAILED:
        raise AssertionError(f"WaitForSingleObject falhou: {ctypes_get_last_error()}")
    assert result in {_WAIT_OBJECT_0, _WAIT_TIMEOUT}
    return result


def ctypes_get_last_error() -> int:
    import ctypes

    return ctypes.get_last_error()


def _open_windows_process_handle(pid: int) -> tuple[Any, Any]:
    kernel32 = _windows_process_api()
    handle = kernel32.OpenProcess(_SYNCHRONIZE | _PROCESS_TERMINATE, False, pid)
    if not handle:
        raise AssertionError(f"OpenProcess falhou para PID {pid}: {ctypes_get_last_error()}")
    return kernel32, handle


def _cleanup_windows_process_handle(kernel32: Any, handle: Any) -> None:
    try:
        state = _wait_for_windows_handle(kernel32, handle, 0)
        if state == _WAIT_TIMEOUT:
            assert kernel32.TerminateProcess(handle, 1), (
                f"TerminateProcess falhou: {ctypes_get_last_error()}"
            )
            assert _wait_for_windows_handle(kernel32, handle, 5000) == _WAIT_OBJECT_0
    finally:
        assert kernel32.CloseHandle(handle), f"CloseHandle falhou: {ctypes_get_last_error()}"


def _require_pidfd() -> None:
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise RuntimeError("Linux pidfd e pidfd_send_signal sao obrigatorios para este teste")


def _wait_for_pidfd(pidfd: int, timeout_ms: int) -> bool:
    poller = select.poll()
    poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
    return bool(poller.poll(timeout_ms))


def _cleanup_pidfd(pidfd: int) -> None:
    try:
        if not _wait_for_pidfd(pidfd, 0):
            signal.pidfd_send_signal(pidfd, signal.SIGKILL, None, 0)  # type: ignore[attr-defined]
            assert _wait_for_pidfd(pidfd, 5000)
    except ProcessLookupError:
        pass
    finally:
        os.close(pidfd)


@pytest.mark.parametrize(
    ("result", "expected"),
    [(_WAIT_OBJECT_0, _WAIT_OBJECT_0), (_WAIT_TIMEOUT, _WAIT_TIMEOUT)],
)
def test_windows_handle_wait_maps_completion_and_timeout(result: int, expected: int) -> None:
    class Kernel32:
        @staticmethod
        def WaitForSingleObject(_handle: object, _timeout: int) -> int:
            return result

    assert _wait_for_windows_handle(Kernel32(), object(), 1) == expected


def test_windows_handle_wait_reports_wait_failed() -> None:
    class Kernel32:
        @staticmethod
        def WaitForSingleObject(_handle: object, _timeout: int) -> int:
            return _WAIT_FAILED

    with pytest.raises(AssertionError, match="WaitForSingleObject"):
        _wait_for_windows_handle(Kernel32(), object(), 1)


@pytest.mark.parametrize(
    ("terminate_ok", "close_ok"),
    [(True, True), (False, True), (True, False)],
)
def test_windows_handle_cleanup_validates_termination_and_close(
    terminate_ok: bool, close_ok: bool
) -> None:
    class Kernel32:
        def __init__(self) -> None:
            self.wait_calls = 0
            self.closed = False

        def WaitForSingleObject(self, _handle: object, _timeout: int) -> int:
            self.wait_calls += 1
            return _WAIT_TIMEOUT if self.wait_calls == 1 else _WAIT_OBJECT_0

        @staticmethod
        def TerminateProcess(_handle: object, _code: int) -> bool:
            return terminate_ok

        def CloseHandle(self, _handle: object) -> bool:
            self.closed = True
            return close_ok

    kernel32 = Kernel32()
    if terminate_ok and close_ok:
        _cleanup_windows_process_handle(kernel32, object())
        assert kernel32.closed
    elif not terminate_ok:
        with pytest.raises(AssertionError, match="TerminateProcess"):
            _cleanup_windows_process_handle(kernel32, object())
        assert kernel32.closed
    else:
        with pytest.raises(AssertionError, match="CloseHandle"):
            _cleanup_windows_process_handle(kernel32, object())
        assert kernel32.closed


def test_pidfd_cleanup_closes_descriptor_after_emergency_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits = iter([False, True])
    sent: list[tuple[int, int]] = []
    closed: list[int] = []

    monkeypatch.setattr(
        sys.modules[__name__],
        "_wait_for_pidfd",
        lambda _pidfd, _timeout: next(waits),
    )
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(
        signal,
        "pidfd_send_signal",
        lambda pidfd, sig, _info, _flags: sent.append((pidfd, sig)),
        raising=False,
    )
    monkeypatch.setattr(os, "close", lambda pidfd: closed.append(pidfd))

    _cleanup_pidfd(42)

    assert sent == [(42, signal.SIGKILL)]
    assert closed == [42]


def test_load_extension_manifest_and_build_descriptors(tmp_path: Path) -> None:
    manifest_path = tmp_path / "extension.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": "demo.extension",
                "version": "1.0.0",
                "protocol_version": "1.0",
                "transport": "stdio",
                "entrypoint": ["python", "echo.py"],
                "timeout_seconds": 5,
                "tools": [
                    {
                        "name": "echo_tool",
                        "description": "echo tool",
                        "schema": {
                            "type": "object",
                            "properties": {"message": {"type": "string"}},
                            "required": ["message"],
                        },
                        "capabilities": ["read"],
                        "cost": 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    manifest = load_extension_manifest(manifest_path)
    adapter = StdioToolAdapter(manifest, cwd=tmp_path)

    descriptors = adapter.descriptors()
    assert len(descriptors) == 1
    assert descriptors[0].name == "echo_tool"
    assert descriptors[0].capabilities == frozenset({"read"})


def test_stdio_adapter_invokes_external_process_and_returns_result(tmp_path: Path) -> None:
    script = tmp_path / "echo.py"
    script.write_text(
        textwrap.dedent(
            """
            import json
            import sys

            def main() -> None:
                payload = json.loads(sys.stdin.readline())
                response = {
                    "type": "result",
                    "invocation_id": payload.get("invocation_id"),
                    "status": "succeeded",
                    "data": payload.get("args", {}).get("message"),
                    "message": "ok",
                }
                sys.stdout.write(json.dumps(response) + "\\n")
                sys.stdout.flush()

            if __name__ == "__main__":
                main()
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    manifest_path = tmp_path / "extension.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": "demo.extension",
                "version": "1.0.0",
                "protocol_version": "1.0",
                "transport": "stdio",
                "entrypoint": ["python", "echo.py"],
                "timeout_seconds": 5,
                "tools": [
                    {
                        "name": "echo_tool",
                        "description": "echo tool",
                        "schema": {
                            "type": "object",
                            "properties": {"message": {"type": "string"}},
                            "required": ["message"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    manifest = load_extension_manifest(manifest_path)
    adapter = StdioToolAdapter(manifest, cwd=tmp_path)

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={"message": "hello"}))

    assert result.ok is True
    assert result.status == ToolStatus.SUCCEEDED
    assert result.data == "hello"
    assert result.message == "ok"
    assert result.invocation_id


def _adapter_for_script(tmp_path: Path, source: str, *, timeout_seconds: int = 5) -> StdioToolAdapter:
    (tmp_path / "script.py").write_text(textwrap.dedent(source).strip() + "\n", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": "test.extension",
                "version": "1.0.0",
                "protocol_version": "1.0",
                "transport": "stdio",
                "entrypoint": ["python", "script.py"],
                "timeout_seconds": timeout_seconds,
                "tools": [{"name": "echo_tool"}],
            }
        ),
        encoding="utf-8",
    )
    return StdioToolAdapter(load_extension_manifest(manifest_path), cwd=tmp_path)


def test_stdio_adapter_rejects_response_without_invocation_id(tmp_path: Path) -> None:
    adapter = _adapter_for_script(
        tmp_path,
        """
        import json
        import sys

        json.loads(sys.stdin.readline())
        print(json.dumps({"status": "succeeded", "data": "ok"}))
        """,
    )

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.PROTOCOL_ERROR
    assert result.error is not None
    assert result.error.code == "MISSING_INVOCATION_ID"


def test_stdio_adapter_rejects_unknown_invocation_id(tmp_path: Path) -> None:
    adapter = _adapter_for_script(
        tmp_path,
        """
        import json
        import sys

        json.loads(sys.stdin.readline())
        print(json.dumps({"invocation_id": "other-call", "status": "succeeded"}))
        """,
    )

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.PROTOCOL_ERROR
    assert result.error is not None
    assert result.error.code == "INVOCATION_MISMATCH"


def test_stdio_adapter_rejects_second_terminal_response(tmp_path: Path) -> None:
    adapter = _adapter_for_script(
        tmp_path,
        """
        import json
        import sys

        payload = json.loads(sys.stdin.readline())
        response = {"invocation_id": payload["invocation_id"], "status": "succeeded"}
        print(json.dumps(response))
        print(json.dumps(response))
        """,
    )

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.PROTOCOL_ERROR
    assert result.error is not None
    assert result.error.code == "INVALID_RESPONSE"


def test_stdio_adapter_discards_late_response_after_timeout(tmp_path: Path) -> None:
    adapter = _adapter_for_script(
        tmp_path,
        """
        import json
        import pathlib
        import sys
        import time

        payload = json.loads(sys.stdin.readline())
        time.sleep(3)
        pathlib.Path("late-response.txt").write_text("late", encoding="utf-8")
        print(json.dumps({"invocation_id": payload["invocation_id"], "status": "succeeded"}))
        """,
        timeout_seconds=1,
    )

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.TIMED_OUT
    assert result.invocation_id
    assert not (tmp_path / "late-response.txt").exists()


def test_stdio_adapter_accepts_failed_response_with_matching_id(tmp_path: Path) -> None:
    adapter = _adapter_for_script(
        tmp_path,
        """
        import json
        import sys

        payload = json.loads(sys.stdin.readline())
        print(json.dumps({
            "invocation_id": payload["invocation_id"],
            "status": "failed",
            "message": "expected failure",
        }))
        """,
    )

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.FAILED
    assert result.error is not None
    assert result.error.code == "TOOL_ERROR"
    assert result.invocation_id


@pytest.mark.parametrize("value", ["", 42, True, None])
def test_stdio_adapter_rejects_invalid_invocation_id_types(tmp_path: Path, value: object) -> None:
    adapter = _adapter_for_script(
        tmp_path,
        f"""
        import json
        import sys

        json.loads(sys.stdin.readline())
        print(json.dumps({{"invocation_id": {value!r}, "status": "succeeded"}}))
        """,
    )

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.PROTOCOL_ERROR
    assert result.error is not None
    assert result.error.code == "MISSING_INVOCATION_ID"


def test_stdio_adapter_accepts_missing_status_as_success(tmp_path: Path) -> None:
    adapter = _adapter_for_script(
        tmp_path,
        """
        import json
        import sys

        payload = json.loads(sys.stdin.readline())
        print(json.dumps({"invocation_id": payload["invocation_id"]}))
        """,
    )

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.SUCCEEDED


@pytest.mark.parametrize("status", ["Succeeded", "unknown", 1, True, None])
def test_stdio_adapter_rejects_unknown_or_typed_status(tmp_path: Path, status: object) -> None:
    adapter = _adapter_for_script(
        tmp_path,
        f"""
        import json
        import sys

        payload = json.loads(sys.stdin.readline())
        print(json.dumps({{"invocation_id": payload["invocation_id"], "status": {status!r}}}))
        """,
    )

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.PROTOCOL_ERROR
    assert result.error is not None
    assert result.error.code == "INVALID_RESPONSE"


def test_stdio_adapter_rejects_debug_text_on_stdout(tmp_path: Path) -> None:
    adapter = _adapter_for_script(
        tmp_path,
        """
        import json
        import sys

        payload = json.loads(sys.stdin.readline())
        print("debug: starting")
        print(json.dumps({"invocation_id": payload["invocation_id"], "status": "succeeded"}))
        """,
    )

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.PROTOCOL_ERROR
    assert result.error is not None
    assert result.error.code == "INVALID_RESPONSE"


def test_stdio_adapter_rejects_malformed_json(tmp_path: Path) -> None:
    adapter = _adapter_for_script(
        tmp_path,
        """
        import json
        import sys

        json.loads(sys.stdin.readline())
        print("{not-json}")
        """,
    )

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.PROTOCOL_ERROR
    assert result.error is not None
    assert result.error.code == "INVALID_RESPONSE"


def test_stdio_adapter_rejects_non_object_json(tmp_path: Path) -> None:
    adapter = _adapter_for_script(
        tmp_path,
        """
        import json
        import sys

        json.loads(sys.stdin.readline())
        print(json.dumps([]))
        """,
    )

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.PROTOCOL_ERROR
    assert result.error is not None
    assert result.error.code == "INVALID_RESPONSE"


@pytest.mark.parametrize(
    ("source", "expected_code"),
    [
        (
            """
            import sys
            sys.stdout.write('x' * 4096)
            sys.stdout.flush()
            """,
            "OUTPUT_LIMIT",
        ),
        (
            """
            import sys
            sys.stderr.write('x' * 4096)
            sys.stderr.flush()
            """,
            "STDERR_OUTPUT_LIMIT",
        ),
    ],
)
def test_stdio_adapter_enforces_stream_limits_during_production(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    expected_code: str,
) -> None:
    monkeypatch.setattr(stdio_adapter_module, "MAX_OUTPUT_BYTES", 1024)
    monkeypatch.setattr(stdio_adapter_module, "MAX_STDERR_BYTES", 1024)
    adapter = _adapter_for_script(tmp_path, source)

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.PROTOCOL_ERROR
    assert result.error is not None
    assert result.error.code == expected_code
    assert result.invocation_id


def test_stdio_adapter_accepts_stdout_exactly_at_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    limit = 256
    monkeypatch.setattr(stdio_adapter_module, "MAX_OUTPUT_BYTES", limit)
    adapter = _adapter_for_script(
        tmp_path,
        f"""
        import json
        import sys

        payload = json.loads(sys.stdin.readline())
        import os

        response = json.dumps({{"invocation_id": payload["invocation_id"], "status": "succeeded"}}).encode("utf-8")
        newline = b"\\r\\n" if os.name == "nt" else b"\\n"
        sys.stdout.buffer.write(response + b" " * ({limit} - len(response) - len(newline)) + newline)
        """,
    )

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.SUCCEEDED


def test_stdio_adapter_accepts_stderr_exactly_at_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    limit = 256
    monkeypatch.setattr(stdio_adapter_module, "MAX_STDERR_BYTES", limit)
    adapter = _adapter_for_script(
        tmp_path,
        f"""
        import json
        import os
        import sys

        payload = json.loads(sys.stdin.readline())
        newline = b"\\r\\n" if os.name == "nt" else b"\\n"
        sys.stderr.buffer.write(b"e" * ({limit} - len(newline)) + newline)
        print(json.dumps({{"invocation_id": payload["invocation_id"], "status": "succeeded"}}))
        """,
    )

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.SUCCEEDED


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_stdio_adapter_rejects_one_byte_above_stream_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream: str
) -> None:
    limit = 256
    monkeypatch.setattr(stdio_adapter_module, "MAX_OUTPUT_BYTES", limit)
    monkeypatch.setattr(stdio_adapter_module, "MAX_STDERR_BYTES", limit)
    adapter = _adapter_for_script(
        tmp_path,
        f"""
        import json
        import sys

        payload = json.loads(sys.stdin.readline())
        response = json.dumps({{"invocation_id": payload["invocation_id"], "status": "succeeded"}}).encode()
        padding = b"x" * ({limit} + 1 - len(response) - 1)
        sys.{stream}.buffer.write(response + padding + b"\\n")
        sys.{stream}.flush()
        """,
    )

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.PROTOCOL_ERROR
    assert result.error is not None
    assert result.error.code == (
        "OUTPUT_LIMIT" if stream == "stdout" else "STDERR_OUTPUT_LIMIT"
    )
    assert result.invocation_id


@pytest.mark.parametrize(
    ("source", "expected_status", "expected_code"),
    [
        (
            """
            import json
            import sys

            payload = json.loads(sys.stdin.readline())
            print(json.dumps({"invocation_id": payload["invocation_id"], "status": "succeeded"}))
            sys.exit(3)
            """,
            ToolStatus.FAILED,
            "PROCESS_FAILED",
        ),
        (
            """
            import json
            import sys

            payload = json.loads(sys.stdin.readline())
            print(json.dumps({"invocation_id": payload["invocation_id"], "status": "failed"}))
            sys.exit(3)
            """,
            ToolStatus.FAILED,
            "PROCESS_FAILED",
        ),
        (
            """
            import json
            import sys

            json.loads(sys.stdin.readline())
            sys.exit(3)
            """,
            ToolStatus.FAILED,
            "PROCESS_FAILED",
        ),
        (
            """
            import json
            import sys

            json.loads(sys.stdin.readline())
            print("not json")
            sys.exit(3)
            """,
            ToolStatus.FAILED,
            "PROCESS_FAILED",
        ),
        (
            """
            import json
            import sys

            json.loads(sys.stdin.readline())
            """
            ,
            ToolStatus.PROTOCOL_ERROR,
            "INVALID_RESPONSE",
        ),
    ],
)
def test_stdio_adapter_preserves_exit_code_and_empty_response_semantics(
    tmp_path: Path,
    source: str,
    expected_status: ToolStatus,
    expected_code: str,
) -> None:
    adapter = _adapter_for_script(tmp_path, source)

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == expected_status
    assert result.error is not None
    assert result.error.code == expected_code


def test_stdio_adapter_drains_stdout_and_stderr_without_deadlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stdio_adapter_module, "MAX_OUTPUT_BYTES", 1024)
    monkeypatch.setattr(stdio_adapter_module, "MAX_STDERR_BYTES", 1024)
    adapter = _adapter_for_script(
        tmp_path,
        """
        import sys

        sys.stdout.write('o' * 4096)
        sys.stderr.write('e' * 4096)
        sys.stdout.flush()
        sys.stderr.flush()
        """,
    )

    started = time.monotonic()
    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert time.monotonic() - started < 5
    assert result.status == ToolStatus.PROTOCOL_ERROR
    assert result.error is not None
    assert result.error.code in {"OUTPUT_LIMIT", "STDERR_OUTPUT_LIMIT"}


def test_stdio_adapter_does_not_leave_reader_threads(tmp_path: Path) -> None:
    adapter = _adapter_for_script(
        tmp_path,
        """
        import json
        import sys

        payload = json.loads(sys.stdin.readline())
        print(json.dumps({"invocation_id": payload["invocation_id"], "status": "succeeded"}))
        """,
    )

    adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert not any(
        thread.is_alive() and thread.name.startswith("stdio-")
        for thread in threading.enumerate()
    )


def test_stdio_adapter_terminates_descendant_before_probe_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready_path = tmp_path / "child-ready.txt"
    watching_path = tmp_path / "child-watching.txt"
    pid_path = tmp_path / "child.pid"
    probe_path = tmp_path / "child-probe.txt"
    late_path = tmp_path / "child-late.txt"
    adapter = _adapter_for_script(
        tmp_path,
        f"""
        import json
        import pathlib
        import subprocess
        import sys
        import time

        json.loads(sys.stdin.readline())
        child = (
            "import pathlib,time\\n"
            f"ready=pathlib.Path(r'{ready_path.as_posix()}')\\n"
            f"watching=pathlib.Path(r'{watching_path.as_posix()}')\\n"
            f"pid_path=pathlib.Path(r'{pid_path.as_posix()}')\\n"
            f"probe=pathlib.Path(r'{probe_path.as_posix()}')\\n"
            f"late=pathlib.Path(r'{late_path.as_posix()}')\\n"
            "pid_path.write_text(str(__import__('os').getpid()))\\n"
            "ready.write_text('ready')\\n"
            "watching.write_text('watching')\\n"
            "deadline=time.monotonic()+60\\n"
            "while not probe.exists() and time.monotonic()<deadline:\\n"
            "    time.sleep(0.01)\\n"
            "if probe.exists():\\n"
            "    late.write_text('late')"
        )
        subprocess.Popen([sys.executable, '-c', child])
        deadline = time.monotonic() + 2
        while not pathlib.Path(r'{watching_path.as_posix()}').exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        while True:
            time.sleep(1)
        """,
        timeout_seconds=1,
    )

    original_monitor = stdio_process_module._monitor_process
    windows_handle: tuple[Any, Any] | None = None
    pidfd: int | None = None

    def monitor_after_watching(*args: Any, **kwargs: Any) -> object:
        nonlocal windows_handle, pidfd
        assert _wait_for_path(watching_path)
        assert _wait_for_path(pid_path)
        pid = _read_test_pid(pid_path)
        if os.name == "nt":
            windows_handle = _open_windows_process_handle(pid)
        else:
            _require_pidfd()
            pidfd = os.pidfd_open(pid, 0)  # type: ignore[attr-defined]
        return original_monitor(*args, **kwargs)

    monkeypatch.setattr(stdio_process_module, "_monitor_process", monitor_after_watching)
    try:
        result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))
        assert result.status == ToolStatus.TIMED_OUT
        assert result.invocation_id
        assert ready_path.exists()
        assert watching_path.exists()
        if windows_handle is not None:
            assert _wait_for_windows_handle(*windows_handle, 1000) == _WAIT_OBJECT_0
        else:
            assert pidfd is not None
            assert _wait_for_pidfd(pidfd, 1000)
        probe_path.write_text("probe", encoding="utf-8")
        assert not _wait_for_path(late_path, 1)
    finally:
        probe_path.write_text("probe", encoding="utf-8")
        if windows_handle is not None:
            _cleanup_windows_process_handle(*windows_handle)
        elif pidfd is not None:
            _cleanup_pidfd(pidfd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups are unavailable on Windows")
def test_stdio_adapter_kills_sigterm_resistant_posix_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready_path = tmp_path / "term-ready.txt"
    watching_path = tmp_path / "term-watching.txt"
    pid_path = tmp_path / "term-child.pid"
    probe_path = tmp_path / "term-probe.txt"
    late_path = tmp_path / "late-posix.txt"
    adapter = _adapter_for_script(
        tmp_path,
        f"""
        import json
        import pathlib
        import signal
        import subprocess
        import sys
        import time

        json.loads(sys.stdin.readline())
        child = (
            "import pathlib,signal,time\\n"
            "signal.signal(signal.SIGTERM, lambda *_: pathlib.Path(r'{(tmp_path / 'term-seen.txt').as_posix()}').write_text('term'))\\n"
            "pathlib.Path(r'{pid_path.as_posix()}').write_text(str(__import__('os').getpid()))\\n"
            "pathlib.Path(r'{ready_path.as_posix()}').write_text('ready')\\n"
            "pathlib.Path(r'{watching_path.as_posix()}').write_text('watching')\\n"
            f"probe=pathlib.Path(r'{probe_path.as_posix()}')\\n"
            f"late=pathlib.Path(r'{late_path.as_posix()}')\\n"
            "deadline=time.monotonic()+60\\n"
            "while not probe.exists() and time.monotonic()<deadline:\\n"
            "    time.sleep(0.01)\\n"
            "if probe.exists():\\n"
            "    late.write_text('late')"
        )
        subprocess.Popen([sys.executable, '-c', child])
        deadline = time.monotonic() + 5
        while not pathlib.Path(r'{watching_path.as_posix()}').exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        while True:
            time.sleep(1)
        """,
        timeout_seconds=1,
    )

    _require_pidfd()
    original_monitor = stdio_process_module._monitor_process
    pidfd: int | None = None

    def monitor_after_watching(*args: Any, **kwargs: Any) -> object:
        nonlocal pidfd
        assert _wait_for_path(watching_path)
        assert ready_path.exists()
        assert _wait_for_path(pid_path)
        pidfd = os.pidfd_open(_read_test_pid(pid_path), 0)  # type: ignore[attr-defined]
        return original_monitor(*args, **kwargs)

    monkeypatch.setattr(stdio_process_module, "_monitor_process", monitor_after_watching)
    try:
        result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))
        assert result.status == ToolStatus.TIMED_OUT
        assert ready_path.exists()
        assert watching_path.exists()
        assert (tmp_path / "term-seen.txt").exists()
        assert pidfd is not None
        assert _wait_for_pidfd(pidfd, 1000)
        probe_path.write_text("probe", encoding="utf-8")
        assert not _wait_for_path(late_path, 1)
    finally:
        probe_path.write_text("probe", encoding="utf-8")
        if pidfd is not None:
            _cleanup_pidfd(pidfd)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are unavailable on POSIX")
# The sentinel is written before stdin is read, so early execution cannot pass.
def test_windows_launcher_waits_for_job_association_before_immediate_extension_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = tmp_path / "extension-started.txt"
    adapter = _adapter_for_script(
        tmp_path,
        f"""
        import json
        import pathlib
        import sys

        pathlib.Path(r'{sentinel.as_posix()}').write_text('started', encoding='utf-8')
        payload = json.loads(sys.stdin.readline())
        print(json.dumps({{"invocation_id": payload["invocation_id"], "status": "succeeded"}}))
        """,
    )
    association_entered = threading.Event()
    release_association = threading.Event()
    original_assign = stdio_process_module.assign_windows_job
    original_popen = subprocess.Popen
    original_send_request = stdio_process_module._send_request
    created_commands: list[list[str]] = []
    sent_requests: list[dict[str, Any]] = []

    def record_popen(command: list[str], **kwargs: Any) -> subprocess.Popen[Any]:
        created_commands.append(command)
        return original_popen(command, **kwargs)

    def record_send_request(process: subprocess.Popen[Any], payload: dict[str, Any]) -> None:
        sent_requests.append(payload)
        original_send_request(process, payload)

    def blocked_assign(job: object, process: object) -> bool:
        association_entered.set()
        assert release_association.wait(5)
        return original_assign(job, process)  # type: ignore[arg-type]

    monkeypatch.setattr(stdio_process_module, "assign_windows_job", blocked_assign)
    monkeypatch.setattr(stdio_process_module.subprocess, "Popen", record_popen)
    monkeypatch.setattr(stdio_process_module, "_send_request", record_send_request)
    result_holder: dict[str, object] = {}
    worker = threading.Thread(
        target=lambda: result_holder.setdefault(
            "result", adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))
        ),
        daemon=True,
    )
    worker.start()
    try:
        assert association_entered.wait(5)
        expected_launcher = [sys.executable, str(Path(stdio_launcher_module.__file__).resolve())]
        # The regression is detected by the command, not by extension scheduling speed.
        assert created_commands == [expected_launcher]
        assert created_commands[0] != ["python", "script.py"]
        assert sent_requests == []
        assert not sentinel.exists()
    finally:
        release_association.set()
        worker.join(timeout=10)

    assert not worker.is_alive()
    result = result_holder["result"]
    assert getattr(result, "status", None) == ToolStatus.SUCCEEDED
    assert sentinel.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are unavailable on POSIX")
def test_windows_association_failure_does_not_start_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = tmp_path / "extension-started.txt"
    adapter = _adapter_for_script(
        tmp_path,
        f"""
        import pathlib
        pathlib.Path(r'{sentinel.as_posix()}').write_text('started', encoding='utf-8')
        """,
    )
    private_status = tmp_path / "private-status.json"
    terminate_calls: list[object] = []
    close_calls: list[object] = []
    original_terminate = stdio_cleanup_module.terminate_process
    original_close = stdio_cleanup_module.close_windows_job

    def track_terminate(*args: object, **kwargs: object) -> str | None:
        terminate_calls.append(args[0] if args else None)
        return original_terminate(*args, **kwargs)  # type: ignore[arg-type]

    def track_close(job: object) -> bool:
        close_calls.append(job)
        return original_close(job)

    monkeypatch.setattr(stdio_launcher_module, "create_status_file", lambda: private_status)
    monkeypatch.setattr(stdio_process_module, "assign_windows_job", lambda *_args: False)
    monkeypatch.setattr(stdio_cleanup_module, "terminate_process", track_terminate)
    monkeypatch.setattr(stdio_cleanup_module, "close_windows_job", track_close)

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.code == "PROCESS_ERROR"
    assert not sentinel.exists()
    assert not private_status.exists()
    assert len(terminate_calls) == 1
    assert len(close_calls) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are unavailable on POSIX")
def test_windows_association_cleanup_termination_failure_is_observable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _adapter_for_script(
        tmp_path,
        "import pathlib; pathlib.Path('extension-started.txt').write_text('started')",
    )
    original_terminate = stdio_cleanup_module.terminate_process

    def fail_terminate(*args: object, **kwargs: object) -> str:
        original_terminate(*args, **kwargs)  # type: ignore[arg-type]
        return "terminate did not confirm cleanup"

    monkeypatch.setattr(stdio_process_module, "assign_windows_job", lambda *_args: False)
    monkeypatch.setattr(stdio_cleanup_module, "terminate_process", fail_terminate)

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.code == "CLEANUP_ERROR"
    assert "falha ao associar launcher" in result.error.message
    assert "terminate did not confirm cleanup" in result.error.message
    assert not (tmp_path / "extension-started.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are unavailable on POSIX")
def test_windows_association_cleanup_job_close_failure_is_observable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _adapter_for_script(
        tmp_path,
        "import pathlib; pathlib.Path('extension-started.txt').write_text('started')",
    )
    original_close = stdio_cleanup_module.close_windows_job

    def fail_close(job: object) -> bool:
        original_close(job)
        return False

    monkeypatch.setattr(stdio_process_module, "assign_windows_job", lambda *_args: False)
    monkeypatch.setattr(stdio_cleanup_module, "close_windows_job", fail_close)

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.code == "CLEANUP_ERROR"
    assert "falha ao associar launcher" in result.error.message
    assert "close Job" in result.error.message
    assert not (tmp_path / "extension-started.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are unavailable on POSIX")
@pytest.mark.parametrize("failure_kind", ["terminate", "close", "both"])
def test_windows_association_cleanup_failures_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_kind: str
) -> None:
    adapter = _adapter_for_script(
        tmp_path,
        "import pathlib; pathlib.Path('extension-started.txt').write_text('started')",
    )
    original_terminate = stdio_cleanup_module.terminate_process
    original_close = stdio_cleanup_module.close_windows_job
    calls: list[str] = []

    def terminate(*args: object, **kwargs: object) -> str | None:
        original_terminate(*args, **kwargs)  # type: ignore[arg-type]
        calls.append("terminate")
        return "terminate failure" if failure_kind in {"terminate", "both"} else None

    def close(job: object) -> bool:
        original_close(job)
        calls.append("close")
        return failure_kind not in {"close", "both"}

    monkeypatch.setattr(stdio_process_module, "assign_windows_job", lambda *_args: False)
    monkeypatch.setattr(stdio_cleanup_module, "terminate_process", terminate)
    monkeypatch.setattr(stdio_cleanup_module, "close_windows_job", close)

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.code == "CLEANUP_ERROR"
    assert len(result.error.message) <= stdio_cleanup_module.MAX_CLEANUP_DETAIL_CHARS
    assert calls == ["terminate", "close"]
    assert "falha ao associar launcher" in result.error.message
    if failure_kind in {"terminate", "both"}:
        assert "terminate failure" in result.error.message
    if failure_kind in {"close", "both"}:
        assert "close Job nao confirmou fechamento" in result.error.message


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are unavailable on POSIX")
def test_windows_association_status_cleanup_failure_preserves_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _adapter_for_script(tmp_path, "import pathlib; pathlib.Path('started').write_text('x')")
    original_remove = stdio_cleanup_module.remove_status_file

    def remove_with_diagnostic(path: Path | None) -> str | None:
        original_remove(path)
        return "status privado nao removido: teste"

    monkeypatch.setattr(stdio_process_module, "assign_windows_job", lambda *_args: False)
    monkeypatch.setattr(stdio_cleanup_module, "remove_status_file", remove_with_diagnostic)

    result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))

    assert result.status == ToolStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.code == "PROCESS_ERROR"
    assert "falha ao associar launcher" in result.error.message
    assert "status privado nao removido" in result.error.message


def test_pre_context_cleanup_accepts_already_absent_resources() -> None:
    original = OSError("association failed")

    first_failure, first_status = stdio_cleanup_module.cleanup_pre_context(None, None, None, original)
    second_failure, second_status = stdio_cleanup_module.cleanup_pre_context(None, None, None, original)

    assert first_failure is None
    assert first_status is None
    assert second_failure is None
    assert second_status is None


def test_pre_context_cleanup_accepts_partially_closed_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ownership released resources are represented as None; a stale raw handle
    # is not a supported input to a second cleanup attempt.
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=5)
    absent_status = tmp_path / "already-removed.status.json"
    original = OSError("association failed")
    terminate_calls: list[subprocess.Popen[Any]] = []
    original_terminate = stdio_cleanup_module.terminate_process

    def record_terminate(
        target: subprocess.Popen[Any], job: object, *, process_group_id: int | None
    ) -> str | None:
        terminate_calls.append(target)
        return original_terminate(target, job, process_group_id=process_group_id)

    monkeypatch.setattr(stdio_cleanup_module, "terminate_process", record_terminate)

    first = stdio_cleanup_module.cleanup_pre_context(process, None, absent_status, original)
    second = stdio_cleanup_module.cleanup_pre_context(None, None, absent_status, original)

    assert first == (None, None)
    assert second == (None, None)
    assert original.args == ("association failed",)
    assert len(terminate_calls) == (0 if os.name == "nt" else 1)


def test_pre_context_cleanup_keeps_terminating_live_processes_observable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=os.name != "nt",
    )
    calls: list[subprocess.Popen[Any]] = []
    original_terminate = stdio_cleanup_module.terminate_process

    def fail_after_termination(
        target: subprocess.Popen[Any], job: object, *, process_group_id: int | None
    ) -> str:
        calls.append(target)
        original_terminate(target, job, process_group_id=process_group_id)
        return "termination confirmation failed"

    monkeypatch.setattr(stdio_cleanup_module, "terminate_process", fail_after_termination)
    try:
        cleanup_detail, status_detail = stdio_cleanup_module.cleanup_pre_context(
            process, None, None, OSError("association failed")
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert calls == [process]
    assert status_detail is None
    assert cleanup_detail is not None
    assert "termination confirmation failed" in cleanup_detail


def test_pre_context_cleanup_closes_job_after_process_already_ended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=5)
    job = object()
    close_calls: list[object] = []

    def close_with_failure(value: object) -> bool:
        close_calls.append(value)
        return False

    monkeypatch.setattr(stdio_cleanup_module, "close_windows_job", close_with_failure)
    cleanup_detail, _ = stdio_cleanup_module.cleanup_pre_context(
        process, job, None, OSError("association failed")
    )

    assert close_calls == [job]
    assert cleanup_detail is not None
    assert "close Job nao confirmou fechamento" in cleanup_detail


def test_pre_context_cleanup_removes_status_for_process_already_ended(tmp_path: Path) -> None:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=5)
    status_path = tmp_path / "private.status"
    status_path.write_text("stale", encoding="utf-8")

    cleanup_detail, status_detail = stdio_cleanup_module.cleanup_pre_context(
        process, None, status_path, OSError("association failed")
    )

    assert cleanup_detail is None
    assert status_detail is None
    assert not status_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are unavailable on POSIX")
def test_windows_launcher_repeated_invocations_cleanup_private_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _adapter_for_script(
        tmp_path,
        """
        import json
        import sys

        payload = json.loads(sys.stdin.readline())
        print(json.dumps({"invocation_id": payload["invocation_id"], "status": "succeeded"}))
        """,
    )
    status_paths: list[Path] = []
    original_create = stdio_launcher_module.create_status_file

    def track_status_file() -> Path:
        path = original_create()
        status_paths.append(path)
        return path

    monkeypatch.setattr(stdio_launcher_module, "create_status_file", track_status_file)

    for _ in range(5):
        result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))
        assert result.status == ToolStatus.SUCCEEDED

    assert len(status_paths) == 5
    assert all(not path.exists() for path in status_paths)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects are unavailable on POSIX")
def test_stdio_adapter_terminates_three_level_windows_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready_path = tmp_path / "grandchild-ready.txt"
    watching_path = tmp_path / "grandchild-watching.txt"
    pid_path = tmp_path / "grandchild.pid"
    probe_path = tmp_path / "grandchild-probe.txt"
    late_path = tmp_path / "grandchild-late.txt"
    adapter = _adapter_for_script(
        tmp_path,
        f"""
        import json
        import pathlib
        import subprocess
        import sys
        import time

        json.loads(sys.stdin.readline())
        grandchild = (
            "import pathlib,time\\n"
            "pathlib.Path(r'{pid_path.as_posix()}').write_text(str(__import__('os').getpid()))\\n"
            "pathlib.Path(r'{ready_path.as_posix()}').write_text('ready')\\n"
            "pathlib.Path(r'{watching_path.as_posix()}').write_text('watching')\\n"
            f"probe=pathlib.Path(r'{probe_path.as_posix()}')\\n"
            f"late=pathlib.Path(r'{late_path.as_posix()}')\\n"
            "deadline=time.monotonic()+60\\n"
            "while not probe.exists() and time.monotonic()<deadline:\\n"
            "    time.sleep(0.01)\\n"
            "if probe.exists():\\n"
            "    late.write_text('late')"
        )
        child = "import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', " + repr(grandchild) + "]); time.sleep(10)"
        subprocess.Popen([sys.executable, '-c', child])
        while True:
            time.sleep(1)
        """,
        timeout_seconds=1,
    )

    original_monitor = stdio_process_module._monitor_process
    windows_handle: tuple[Any, Any] | None = None

    def monitor_after_watching(*args: Any, **kwargs: Any) -> object:
        nonlocal windows_handle
        assert _wait_for_path(watching_path)
        assert ready_path.exists()
        assert _wait_for_path(pid_path)
        windows_handle = _open_windows_process_handle(_read_test_pid(pid_path))
        return original_monitor(*args, **kwargs)

    monkeypatch.setattr(stdio_process_module, "_monitor_process", monitor_after_watching)
    started = time.monotonic()
    try:
        result = adapter.invoke(ToolInvocation(tool_name="echo_tool", args={}))
        assert time.monotonic() - started < 8
        assert result.status == ToolStatus.TIMED_OUT
        assert ready_path.exists()
        assert watching_path.exists()
        assert windows_handle is not None
        assert _wait_for_windows_handle(*windows_handle, 1000) == _WAIT_OBJECT_0
        probe_path.write_text("probe", encoding="utf-8")
        assert not _wait_for_path(late_path, 1)
    finally:
        probe_path.write_text("probe", encoding="utf-8")
        if windows_handle is not None:
            _cleanup_windows_process_handle(*windows_handle)


def test_stdio_reader_cleanup_failure_is_observable(monkeypatch: pytest.MonkeyPatch) -> None:
    stop = threading.Event()
    reader = threading.Thread(target=stop.wait, daemon=True, name="stdio-stuck-reader")
    process = type("Process", (), {"stdin": None, "stdout": None, "stderr": None})()
    context = stdio_process_module._ProcessContext(
        process=process,
        windows_job=None,
        readers=[reader],
        stdout=stdio_process_module._StreamCapture(16),
        stderr=stdio_process_module._StreamCapture(16),
        stop_readers=threading.Event(),
        reader_errors=[],
    )
    reader.start()

    failure = stdio_process_module._join_readers(context)

    stop.set()
    reader.join(timeout=1)
    assert failure is not None
    assert failure.code == "CLEANUP_ERROR"


def test_stdio_reader_errors_are_observable() -> None:
    class FailingStream:
        def read(self, _: int) -> bytes:
            raise RuntimeError("read failed")

    errors: list[str] = []
    stdio_process_module._drain_stream(
        FailingStream(),
        stdio_process_module._StreamCapture(16),
        threading.Event(),
        errors,
    )

    assert errors == ["RuntimeError: read failed"]


def test_stdio_tree_cleanup_failure_is_observable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = type("Process", (), {"stdin": None, "stdout": None, "stderr": None})()
    context = stdio_process_module._ProcessContext(
        process=process,
        windows_job=None,
        readers=[],
        stdout=stdio_process_module._StreamCapture(16),
        stderr=stdio_process_module._StreamCapture(16),
    )
    monkeypatch.setattr(
        stdio_process_module,
        "terminate_process",
        lambda *_args, **_kwargs: "tree termination failed",
    )

    failure = stdio_process_module._cleanup(context, terminate_tree=True)

    assert failure is not None
    assert failure.code == "CLEANUP_ERROR"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", 1),
        ("version", 1),
        ("protocol_version", 1),
        ("transport", 1),
        ("entrypoint", "python"),
        ("tools", {}),
        ("timeout_seconds", "5"),
    ],
)
def test_load_extension_manifest_rejects_coercive_types(
    tmp_path: Path, field: str, value: object
) -> None:
    payload: dict[str, object] = {
        "id": "test.extension",
        "version": "1.0.0",
        "protocol_version": "1.0",
        "transport": "stdio",
        "entrypoint": [sys.executable, "script.py"],
        "timeout_seconds": 5,
        "tools": [{"name": "echo_tool"}],
    }
    payload[field] = value
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_extension_manifest(manifest_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [("name", 1), ("schema", []), ("capabilities", "read"), ("cost", "1")],
)
def test_load_extension_manifest_rejects_malformed_tool_fields(
    tmp_path: Path, field: str, value: object
) -> None:
    tool: dict[str, object] = {"name": "echo_tool"}
    tool[field] = value
    payload = {
        "id": "test.extension",
        "version": "1.0.0",
        "protocol_version": "1.0",
        "entrypoint": [sys.executable, "script.py"],
        "tools": [tool],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_extension_manifest(manifest_path)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.pop("id"),
        lambda payload: payload.update(protocol_version="2.0"),
        lambda payload: payload.pop("transport"),
        lambda payload: payload.pop("timeout_seconds"),
        lambda payload: payload.update(transport="socket"),
        lambda payload: payload.update(entrypoint=[""]),
        lambda payload: payload.update(id="   "),
        lambda payload: payload.update(entrypoint=["   "]),
    ],
)
def test_load_extension_manifest_rejects_invalid_protocol_fields(
    tmp_path: Path, mutation: Callable[[dict[str, object]], object]
) -> None:
    payload: dict[str, object] = {
        "id": "test.extension",
        "version": "1.0.0",
        "protocol_version": "1.0",
        "transport": "stdio",
        "entrypoint": [sys.executable, "script.py"],
        "timeout_seconds": 5,
        "tools": [{"name": "echo_tool"}],
    }
    mutation(payload)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_extension_manifest(manifest_path)


def test_stdio_manifest_rejects_negative_cost(tmp_path: Path) -> None:
    payload = {
        "id": "test.extension",
        "version": "1.0.0",
        "protocol_version": "1.0",
        "transport": "stdio",
        "entrypoint": [sys.executable, "script.py"],
        "timeout_seconds": 5,
        "tools": [{"name": "echo_tool", "cost": -1}],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_extension_manifest(manifest_path)


def _valid_manifest_payload() -> dict[str, object]:
    return {
        "id": "test.extension",
        "version": "1.0.0",
        "protocol_version": "1.0",
        "transport": "stdio",
        "entrypoint": [sys.executable, "script.py"],
        "timeout_seconds": 5,
        "tools": [{"name": "echo_tool"}],
    }


def test_manifest_rejects_unknown_root_field(tmp_path: Path) -> None:
    payload = _valid_manifest_payload()
    payload["unexpected"] = True
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Campos desconhecidos em manifest: unexpected"):
        load_extension_manifest(manifest_path)


def test_manifest_rejects_unknown_tool_field(tmp_path: Path) -> None:
    payload = _valid_manifest_payload()
    payload["tools"][0]["unexpected"] = True  # type: ignore[index]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Campos desconhecidos em tool: unexpected"):
        load_extension_manifest(manifest_path)


def test_manifest_unknown_fields_are_reported_in_sorted_order(tmp_path: Path) -> None:
    payload = _valid_manifest_payload()
    payload["zeta"] = True
    payload["alpha"] = True
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="Campos desconhecidos em manifest: alpha, zeta",
    ):
        load_extension_manifest(manifest_path)


def test_manifest_schema_allows_arbitrary_json_schema_fields(tmp_path: Path) -> None:
    payload = _valid_manifest_payload()
    payload["tools"][0]["schema"] = {  # type: ignore[index]
        "type": "object",
        "x-vendor": {"nested": [1, True, "value"]},
        "additionalProperties": {"type": "string"},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = load_extension_manifest(manifest_path)

    assert manifest.tools[0]["schema"]["x-vendor"] == {
        "nested": [1, True, "value"]
    }


def test_example_manifest_remains_valid() -> None:
    manifest_path = (
        Path(__file__).resolve().parents[3]
        / "examples"
        / "extensions"
        / "demo_extension"
        / "manifest.json"
    )

    manifest = load_extension_manifest(manifest_path)

    assert manifest.id == "demo.extension"


def test_direct_manifest_descriptor_does_not_coerce_fields() -> None:
    manifest = ExtensionManifest(
        id="test.extension",
        version="1.0.0",
        protocol_version="1.0",
        transport="stdio",
        entrypoint=(sys.executable, "script.py"),
        timeout_seconds=5,
        tools=({"name": "echo_tool", "cost": "1"},),
    )

    with pytest.raises(ValueError):
        StdioToolAdapter(manifest).descriptors()
