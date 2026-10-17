"""Incremental redaction across read boundaries, with bounded memory and disk."""
from __future__ import annotations

import codecs
import locale
import os
import threading
from pathlib import Path
from typing import BinaryIO

from scripts.w23_diagnostics.evidence import Redactor

LIMIT = 2 * 1024 * 1024
MAX_LINE = 16 * 1024


class Capture:
    def __init__(self, fd: int, path: Path, redactor: Redactor) -> None:
        self.fd, self.path, self.redactor = fd, path, redactor
        self.raw = bytearray()
        self.pending = ""
        self.written = 0
        self.limited = False
        self.discard_line = False
        self.error = False
        self.decoder = codecs.getincrementaldecoder(locale.getpreferredencoding(False))(errors="replace")
        self.thread = threading.Thread(target=self._read, daemon=True)

    def _write(self, line: str, stream: BinaryIO) -> None:
        safe = self.redactor.text(line).encode("utf-8")
        remaining = max(0, LIMIT - self.written)
        if len(safe) > remaining:
            self.limited = True
            return  # Never publish only a prefix of a redacted record.
        stream.write(safe)
        stream.flush()
        self.written += len(safe)

    def _consume(self, text: str, stream: BinaryIO) -> None:
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            if not self.discard_line and len(line) <= MAX_LINE:
                self._write(line + "\n", stream)
            else:
                self.limited = True
            self.discard_line = False
        if len(self.pending) > MAX_LINE:
            self.pending = ""
            self.discard_line = self.limited = True

    def _read(self) -> None:
        try:
            with self.path.open("xb") as stream:
                self._read_blocks(stream)
        except BaseException:
            self.error = True
        finally:
            os.close(self.fd)

    def _read_blocks(self, stream: BinaryIO) -> None:
        while True:
            block = os.read(self.fd, 4096)
            if not block:
                break
            remaining = max(0, LIMIT - len(self.raw))
            self.raw.extend(block[:remaining])
            self.limited |= len(block) > remaining
            self._consume(self.decoder.decode(block), stream)
        self._consume(self.decoder.decode(b"", final=True), stream)
        if self.pending and not self.discard_line:
            self._write(self.pending, stream)

    def text(self) -> str:
        return bytes(self.raw).decode(locale.getpreferredencoding(False), errors="replace")
