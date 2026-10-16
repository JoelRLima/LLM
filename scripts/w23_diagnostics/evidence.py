"""Bounded public evidence; environment values are never serialized."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

STATES = frozenset({"PASS", "FAIL", "TIMEOUT", "UNKNOWN"})


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    with pending.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, path)


class Redactor:
    def __init__(self, roots: list[Path] | None = None) -> None:
        self.secrets = sorted({
            value for key, value in os.environ.items()
            if re.search(r"TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH|KEY", key, re.I) and len(value) >= 8
        }, key=len, reverse=True)
        paths = list(roots or [])
        paths.extend(Path(os.environ[key]) for key in ("RUNNER_TEMP", "LOCALAPPDATA", "USERPROFILE") if key in os.environ)
        self.paths = sorted({str(p) for p in paths}, key=len, reverse=True)

    def text(self, value: str) -> str:
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
            for fragment in secret.splitlines():
                if len(fragment) >= 8:
                    value = value.replace(fragment, "[REDACTED]")
        value = re.sub(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", value)
        value = re.sub(r"(?i)((?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", value)
        value = re.sub(r"(?i)([?&](?:token|key|password|secret|authorization)=)[^&\s]+", r"\1[REDACTED]", value)
        value = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[REDACTED_QUERY]", value)
        value = re.sub(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})", "[REDACTED]", value)
        value = re.sub(r"""(?i)((?:api[-_]?key|password|secret|access_token)["']?\s*[:=]\s*["']?)[^\s"',;]+""", r"\1[REDACTED]", value)
        for path in self.paths:
            value = value.replace(path, "<RUNNER_PATH>").replace(path.replace("\\", "/"), "<RUNNER_PATH>")
        return value

    def value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {str(key): self.value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.value(item) for item in value]
        return value


class Events:
    def __init__(self, path: Path, redactor: Redactor) -> None:
        self.path, self.redactor = path, redactor
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **fields: Any) -> None:
        record = self.redactor.value(dict(event=event, pid=os.getpid(), wall=time.time(), cpu=time.process_time(), **fields))
        with self.lock, self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        print(event, flush=True)


def inventory(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if root.is_symlink() or getattr(root.lstat(), "st_file_attributes", 0) & 0x400:
        raise ValueError("reparse source root")
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if name != ".git")
        for name in [*names, *files]:
            path = Path(directory) / name
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("reparse point in diagnostic source")
        for name in sorted(files):
            path = Path(directory) / name
            result[path.relative_to(root).as_posix()] = digest(path)
    return result
