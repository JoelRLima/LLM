import io
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

import agent.tools.stdio_launcher as stdio_launcher_module
from agent.tools.stdio_launcher import _validate_envelope


class _BlockingLauncherInput:
    def __init__(self) -> None:
        self.buffer = self
        self.readline_entered = threading.Event()
        self.release_readline = threading.Event()
        self.line = b""

    def readline(self, _limit: int) -> bytes:
        self.readline_entered.set()
        assert self.release_readline.wait(5)
        return self.line

    def read(self, _size: int) -> bytes:
        return b""


class _CompletedExtension:
    def __init__(self, command: list[str]) -> None:
        self.args = command
        self.stdin = io.BytesIO()
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def poll(self) -> int:
        return 0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(launcher_protocol=True),
        lambda value: value.update(command=[]),
        lambda value: value.update(command=[" "]),
        lambda value: value.update(request_line="{\"x\": 1}\n"),
        lambda value: value.update(status_path=""),
        lambda value: value.update(extra=True),
    ],
)
def test_launcher_rejects_invalid_private_envelope(mutation: object) -> None:
    envelope: dict[str, object] = {
        "launcher_protocol": 1,
        "command": [sys.executable, "extension.py"],
        "request_line": "{}",
        "status_path": "C:/private/status.json",
    }
    mutation(envelope)  # type: ignore[operator]

    with pytest.raises(ValueError):
        _validate_envelope(envelope)


def test_launcher_forwards_streams_and_writes_status(tmp_path: Path) -> None:
    extension = tmp_path / "extension.py"
    extension.write_text(
        "import sys\n"
        "request = sys.stdin.readline().strip()\n"
        "sys.stderr.write('diagnostic\\n')\n"
        "sys.stdout.write(request + '\\n')\n"
        "sys.stdout.flush()\n",
        encoding="utf-8",
    )
    status_path = tmp_path / "status.json"
    launcher = Path(__import__("agent.tools.stdio_launcher", fromlist=["__file__"]).__file__).resolve()
    process = subprocess.Popen(
        [sys.executable, str(launcher)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=tmp_path,
        env=os.environ.copy(),
        text=False,
    )
    envelope = {
        "launcher_protocol": 1,
        "command": [sys.executable, str(extension)],
        "request_line": json.dumps({"invocation_id": "call-1"}),
        "status_path": str(status_path),
    }
    stdout, stderr = process.communicate(
        (json.dumps(envelope, separators=(",", ":")) + "\n").encode("utf-8"), timeout=10
    )

    assert process.returncode == 0
    newline = os.linesep.encode("ascii")
    assert stdout == b'{"invocation_id": "call-1"}' + newline
    assert stderr == b"diagnostic" + newline
    assert json.loads(status_path.read_text(encoding="utf-8")) == {"state": "extension_started"}


def test_launcher_tolerates_stdin_close_after_extension_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Stdin:
        def write(self, _: bytes) -> int:
            return 1

        def close(self) -> None:
            raise OSError(22, "Invalid argument")

    class _ExitedProcess:
        def __init__(self) -> None:
            self.stdin = _Stdin()
            self.returncode = 0

        def poll(self) -> int:
            return self.returncode

        def wait(self) -> int:
            return self.returncode

    process = _ExitedProcess()
    status_path = tmp_path / "status.json"
    envelope = {
        "launcher_protocol": 1,
        "command": [sys.executable, "extension.py"],
        "request_line": "{}",
        "status_path": str(status_path),
    }
    monkeypatch.setattr(stdio_launcher_module.subprocess, "Popen", lambda *_args, **_kwargs: process)

    assert stdio_launcher_module._run(envelope) == 0
    assert json.loads(status_path.read_text(encoding="utf-8")) == {"state": "extension_started"}


def test_launcher_does_not_tolerate_stdin_write_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Stdin:
        def write(self, _: bytes) -> int:
            raise OSError(22, "Invalid argument")

        def close(self) -> None:
            raise AssertionError("close must not run after write failure")

    class _ExitedProcess:
        def __init__(self) -> None:
            self.stdin = _Stdin()
            self.returncode = 0

        def poll(self) -> int:
            return self.returncode

    process = _ExitedProcess()
    status_path = tmp_path / "status.json"
    envelope = {
        "launcher_protocol": 1,
        "command": [sys.executable, "extension.py"],
        "request_line": "{}",
        "status_path": str(status_path),
    }
    monkeypatch.setattr(stdio_launcher_module.subprocess, "Popen", lambda *_args, **_kwargs: process)

    assert stdio_launcher_module._run(envelope) == 1
    assert json.loads(status_path.read_text(encoding="utf-8")) == {
        "state": "launcher_error",
        "code": "EXTENSION_START_FAILED",
        "message": "[Errno 22] Invalid argument",
    }


def test_launcher_without_envelope_stays_silent_and_fails() -> None:
    launcher = Path(__import__("agent.tools.stdio_launcher", fromlist=["__file__"]).__file__).resolve()
    process = subprocess.Popen(
        [sys.executable, str(launcher)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate(b"", timeout=10)

    assert process.returncode != 0
    assert stdout == b""
    assert stderr == b""


def test_launcher_does_not_create_extension_before_reading_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocking_input = _BlockingLauncherInput()
    status_path = tmp_path / "status.json"
    extension_command = [sys.executable, "extension.py"]
    envelope = {
        "launcher_protocol": 1,
        "command": extension_command,
        "request_line": "{}",
        "status_path": str(status_path),
    }
    created_commands: list[list[str]] = []

    def record_popen(command: list[str], **_kwargs: Any) -> _CompletedExtension:
        created_commands.append(command)
        return _CompletedExtension(command)

    monkeypatch.setattr(stdio_launcher_module.sys, "stdin", blocking_input)
    monkeypatch.setattr(stdio_launcher_module.subprocess, "Popen", record_popen)
    result: list[int] = []
    worker = threading.Thread(target=lambda: result.append(stdio_launcher_module.main()))
    worker.start()
    try:
        assert blocking_input.readline_entered.wait(5)
        assert created_commands == []
        blocking_input.line = (json.dumps(envelope) + "\n").encode("utf-8")
    finally:
        blocking_input.release_readline.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert result == [0]
    assert created_commands == [extension_command]


def test_launcher_reports_invalid_envelope_in_private_status(tmp_path: Path) -> None:
    launcher = Path(__import__("agent.tools.stdio_launcher", fromlist=["__file__"]).__file__).resolve()
    status_path = tmp_path / "invalid-status.json"
    process = subprocess.Popen(
        [sys.executable, str(launcher)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    invalid = {
        "launcher_protocol": 1,
        "command": [],
        "request_line": "{}",
        "status_path": str(status_path),
    }
    stdout, stderr = process.communicate(
        (json.dumps(invalid) + "\n").encode("utf-8"), timeout=10
    )

    assert process.returncode != 0
    assert stdout == b""
    assert stderr == b""
    assert json.loads(status_path.read_text(encoding="utf-8"))["code"] == "INVALID_ENVELOPE"
