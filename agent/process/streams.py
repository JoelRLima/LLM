"""Bounded stream capture and reader lifecycle for stdio extensions."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from threading import Event, Thread
from typing import Any

STREAM_CHUNK_BYTES = 64 * 1024


@dataclass
class StreamCapture:
    limit: int
    content: bytearray = field(default_factory=bytearray)
    received: int = 0
    exceeded: bool = False

    def append(self, chunk: bytes) -> None:
        self.received += len(chunk)
        if len(self.content) < self.limit:
            self.content.extend(chunk[: self.limit - len(self.content)])
        if self.received > self.limit:
            self.exceeded = True


def drain_stream(
    stream: Any,
    capture: StreamCapture,
    stop_readers: Event | None = None,
    reader_errors: list[str] | None = None,
) -> None:
    stop_event = stop_readers or Event()
    try:
        while not stop_event.is_set():
            chunk = stream.read(STREAM_CHUNK_BYTES)
            if not chunk:
                return
            capture.append(chunk)
    except Exception as exc:
        if not stop_event.is_set() and reader_errors is not None:
            reader_errors.append(f"{type(exc).__name__}: {exc}")


def start_readers(
    process: subprocess.Popen[Any], stdout_limit: int, stderr_limit: int
) -> tuple[list[Thread], StreamCapture, StreamCapture, Event, list[str]]:
    """Start the two bounded readers used by a stdio process."""

    if process.stdout is None or process.stderr is None:
        raise OSError("pipes da extensao nao foram criados")
    stdout = StreamCapture(stdout_limit)
    stderr = StreamCapture(stderr_limit)
    stop_readers = Event()
    reader_errors: list[str] = []
    readers = [
        Thread(
            target=drain_stream,
            args=(process.stdout, stdout, stop_readers, reader_errors),
            daemon=True,
            name="stdio-stdout-drain",
        ),
        Thread(
            target=drain_stream,
            args=(process.stderr, stderr, stop_readers, reader_errors),
            daemon=True,
            name="stdio-stderr-drain",
        ),
    ]
    for reader in readers:
        reader.start()
    return readers, stdout, stderr, stop_readers, reader_errors


def close_pipes(process: subprocess.Popen[Any]) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


def send_request(process: subprocess.Popen[Any], payload: dict[str, Any]) -> None:
    if process.stdin is None:
        raise OSError("stdin da extensão não foi criado")
    request = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
    try:
        process.stdin.write(request)
        process.stdin.close()
    except (BrokenPipeError, OSError):
        try:
            process.stdin.close()
        except OSError:
            pass
