"""Publish only bounded redacted evidence, never private trees, env, or raw captures."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from scripts.w23_diagnostics.evidence import STATES, Redactor, digest, write_json

MAX_TOTAL = 128 * 1024 * 1024
MAX_FILE = 4 * 1024 * 1024


def terminal_summary(value: dict[str, Any], outcome: str) -> dict[str, Any]:
    result = dict(value)
    if result.get("status") not in STATES or not result.get("completed"):
        result.update(status="UNKNOWN", reason="no complete terminal diagnostic")
    if outcome != "success" and result.get("status") == "PASS":
        result.update(status="UNKNOWN", reason="workflow step did not succeed")
    result["w23"] = "BLOCKED"
    return result


def public_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    if root.is_symlink() or getattr(root.lstat(), "st_file_attributes", 0) & 0x400:
        raise ValueError("reparse public root")
    for directory, names, files in os.walk(root, followlinks=False):
        for name in [*names, *files]:
            path = Path(directory) / name
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("reparse point in public evidence")
        paths.extend(Path(directory) / name for name in files)
    if len(paths) > 256 or sum(path.stat().st_size for path in paths) > MAX_TOTAL:
        raise ValueError("evidence exceeds aggregate cap")
    for path in paths:
        if path.suffix not in {".json", ".jsonl", ".log", ".txt"} or path.stat().st_size > MAX_FILE:
            raise ValueError("unexpected public evidence")
    return sorted(paths)


def redact(path: Path, target: Path, redactor: Redactor) -> None:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        write_json(target, redactor.value(json.loads(text)))
        return
    if path.suffix == ".jsonl":
        text = "".join(json.dumps(redactor.value(json.loads(line)), sort_keys=True) + "\n"
                       for line in text.splitlines() if line)
    else:
        text = redactor.text(text)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def export(work: Path, outcome: str) -> int:
    publish, upload = work / "publish", work / "upload"
    upload.mkdir(exist_ok=False)
    redactor = Redactor([work])
    summary: dict[str, Any] = {"status": "UNKNOWN", "completed": False, "w23": "BLOCKED"}
    try:
        summary = terminal_summary(json.loads((publish / "summary.json").read_text(encoding="utf-8")), outcome)
        for source in public_files(publish):
            if source.name != "summary.json":
                redact(source, upload / source.relative_to(publish), redactor)
    except BaseException as exc:
        summary = {"status": "UNKNOWN", "completed": False, "w23": "BLOCKED",
                   "export_error": type(exc).__name__}
    write_json(upload / "summary.json", redactor.value(summary))
    hashes = {path.relative_to(upload).as_posix(): digest(path) for path in sorted(upload.rglob("*")) if path.is_file()}
    write_json(upload / "SHA256.json", hashes)
    print(summary["status"], flush=True)
    return 0 if summary["status"] == "PASS" else 1
