"""Trusted Windows bootstrapper for stdio extension processes.

The adapter starts this module before it starts an extension.  The launcher
does no extension-related work until it receives one validated private
envelope, which lets the adapter associate the launcher with its Job Object
first.  The module intentionally depends only on the standard library and
never writes control data to stdout or stderr.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

LAUNCHER_PROTOCOL = 1
MAX_ENVELOPE_BYTES = 4 * 1024 * 1024
MAX_STATUS_BYTES = 4096
MAX_STATUS_MESSAGE_BYTES = 512
_REQUIRED_FIELDS = {"launcher_protocol", "command", "request_line", "status_path"}


class _EnvelopeError(ValueError):
    def __init__(self, message: str, status_path: Path | None = None) -> None:
        super().__init__(message)
        self.status_path = status_path


def _bounded_message(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ")[:MAX_STATUS_MESSAGE_BYTES]


def _write_status(status_path: Path, payload: dict[str, str]) -> None:
    """Publish a small status object atomically beside the private path."""

    status_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{status_path.name}.",
            suffix=".tmp",
            dir=str(status_path.parent),
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, status_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def create_status_file() -> Path:
    descriptor, name = tempfile.mkstemp(prefix="agent-stdio-", suffix=".status.json")
    os.close(descriptor)
    path = Path(name)
    path.unlink()
    return path


def remove_status_file(status_path: Path | None) -> str | None:
    if status_path is None:
        return None
    try:
        status_path.unlink()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return _bounded_message(f"status privado nao removido: {exc}")
    return None


def build_launcher_envelope(
    entrypoint: tuple[str, ...], payload: dict[str, Any], status_path: Path
) -> dict[str, Any]:
    return {
        "launcher_protocol": LAUNCHER_PROTOCOL,
        "command": list(entrypoint),
        "request_line": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "status_path": str(status_path),
    }


def prepare_launcher(entrypoint: tuple[str, ...]) -> tuple[tuple[str, ...], Path | None]:
    if os.name == "nt":
        return (sys.executable, str(Path(__file__).resolve())), create_status_file()
    return entrypoint, None


def read_launcher_status(status_path: Path) -> dict[str, str]:
    raw = status_path.read_bytes()
    if not raw or len(raw) > MAX_STATUS_BYTES:
        raise ValueError("status privado ausente ou grande demais")
    try:
        status = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(status, dict):
        raise ValueError("status privado nao e objeto")
    state = status.get("state")
    if state == "extension_started" and set(status) == {"state"}:
        return {"state": "extension_started"}
    if state == "launcher_error":
        code = status.get("code")
        message = status.get("message")
        if (
            set(status) == {"state", "code", "message"}
            and isinstance(code, str)
            and code.strip()
            and isinstance(message, str)
            and message.strip()
        ):
            return {"state": "launcher_error", "code": code, "message": message[:MAX_STATUS_MESSAGE_BYTES]}
    raise ValueError("status privado invalido")


def launcher_status_error(status_path: Path) -> tuple[str, str] | None:
    try:
        status = read_launcher_status(status_path)
    except (OSError, ValueError) as exc:
        return "LAUNCHER_STATUS_ERROR", f"status privado invalido: {exc}"
    if status["state"] == "extension_started":
        return None
    if status["state"] == "launcher_error":
        return status["code"], status["message"]
    return "LAUNCHER_STATUS_ERROR", "status privado invalido"


def _validate_envelope(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _REQUIRED_FIELDS:
        raise ValueError("envelope privado invalido")
    protocol = payload["launcher_protocol"]
    if not isinstance(protocol, int) or isinstance(protocol, bool) or protocol != LAUNCHER_PROTOCOL:
        raise ValueError("launcher_protocol invalido")
    command = payload["command"]
    if not isinstance(command, list) or not command or any(
        not isinstance(item, str) or not item.strip() for item in command
    ):
        raise ValueError("command invalido")
    request_line = payload["request_line"]
    if not isinstance(request_line, str) or not request_line or "\n" in request_line or "\r" in request_line:
        raise ValueError("request_line invalido")
    status_path = payload["status_path"]
    if not isinstance(status_path, str) or not status_path.strip():
        raise ValueError("status_path invalido")
    return payload


def _read_envelope() -> dict[str, Any]:
    raw = sys.stdin.buffer.readline(MAX_ENVELOPE_BYTES + 1)
    if not raw or len(raw) > MAX_ENVELOPE_BYTES or not raw.endswith(b"\n"):
        raise _EnvelopeError("envelope privado ausente ou grande demais")
    if sys.stdin.buffer.read(1):
        raise _EnvelopeError("envelope privado deve conter uma linha")
    try:
        payload = json.loads(raw[:-1].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _EnvelopeError("envelope privado nao e JSON UTF-8") from exc
    status_path = (
        Path(payload["status_path"])
        if isinstance(payload, dict) and isinstance(payload.get("status_path"), str)
        else None
    )
    try:
        return _validate_envelope(payload)
    except ValueError as exc:
        raise _EnvelopeError(str(exc), status_path) from exc


def _terminate_child(process: subprocess.Popen[Any]) -> None:
    try:
        process.terminate()
    except OSError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _run(envelope: dict[str, Any]) -> int:
    status_path = Path(envelope["status_path"])
    process: subprocess.Popen[Any] | None = None
    try:
        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0
        process = subprocess.Popen(
            envelope["command"],
            stdin=subprocess.PIPE,
            stdout=None,
            stderr=None,
            cwd=None,
            env=None,
            shell=False,
            creationflags=creationflags,
        )
        _write_status(status_path, {"state": "extension_started"})
        if process.stdin is None:
            raise OSError("stdin da extension nao foi criado")
        process.stdin.write(envelope["request_line"].encode("utf-8") + b"\n")
        process.stdin.close()
        return process.wait()
    except Exception as exc:
        if process is not None and process.poll() is None:
            _terminate_child(process)
        try:
            _write_status(
                status_path,
                {
                    "state": "launcher_error",
                    "code": "EXTENSION_START_FAILED",
                    "message": _bounded_message(str(exc) or type(exc).__name__),
                },
            )
        except OSError:
            pass
        return 1


def main() -> int:
    status_path: Path | None = None
    try:
        envelope = _read_envelope()
        status_path = Path(envelope["status_path"])
        return _run(envelope)
    except _EnvelopeError as exc:
        status_path = exc.status_path
        if status_path is not None:
            try:
                _write_status(
                    status_path,
                    {
                        "state": "launcher_error",
                        "code": "INVALID_ENVELOPE",
                        "message": _bounded_message(str(exc) or type(exc).__name__),
                    },
                )
            except OSError:
                pass
        return 1
    except Exception as exc:
        if status_path is not None:
            try:
                _write_status(
                    status_path,
                    {
                        "state": "launcher_error",
                        "code": "INVALID_ENVELOPE",
                        "message": _bounded_message(str(exc) or type(exc).__name__),
                    },
                )
            except OSError:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
