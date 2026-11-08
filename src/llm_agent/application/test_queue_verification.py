"""Independent, bounded file checks. Model statements never satisfy these checks."""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from llm_agent.application.test_queue_manifest import LIMIT, local_path, parse_json
from llm_agent.execution.command import CommandExecutor, CommandRequest


def _python_evidence(
    workspace: Path,
    check: dict[str, Any],
    index: int,
    cancellation: object | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    script = local_path(workspace.parent, f"verifier-{index}.py")
    script.write_text(check["expected"], encoding="utf-8", newline="")
    result = CommandExecutor().execute(CommandRequest(
        sys.executable, ("-I", str(script), str(workspace)), cwd=workspace,
        timeout_seconds=timeout_seconds, cancellation=cancellation,
        stdout_limit=4096, stderr_limit=4096))
    return {
        "passed": result.completed and result.return_code == 0,
        "return_code": result.return_code,
        "timed_out": result.timed_out,
        "cancelled": result.cancelled,
        "stdout": result.stdout.decode("utf-8", errors="replace"),
        "stderr": result.stderr.decode("utf-8", errors="replace"),
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
        "verifier_sha256": hashlib.sha256(check["expected"].encode()).hexdigest(),
    }


def _file_evidence(path: Path, check: dict[str, Any]) -> dict[str, Any]:
    if check["kind"] == "absent":
        return {"passed": not path.exists()}
    if check["kind"] == "exists":
        return {"passed": path.is_file()}
    if not path.is_file():
        raise FileNotFoundError("Expected result file was not produced")
    if path.stat().st_size > LIMIT:
        raise ValueError("Result file larger than 1 MiB")
    raw = path.read_bytes()
    actual_hash = hashlib.sha256(raw).hexdigest()
    if check["kind"] == "sha256":
        passed = actual_hash == check["expected"]
    elif check["kind"] == "text_equals":
        passed = raw.decode("utf-8") == check["expected"]
    elif check["kind"] == "json_equals":
        # Canonical JSON preserves the distinction between true and 1.
        actual = parse_json(raw.decode("utf-8"))
        passed = json.dumps(actual, sort_keys=True, allow_nan=False) == json.dumps(
            check["expected"], sort_keys=True, allow_nan=False
        )
    else:
        raise ValueError("Unknown check")
    return {"passed": passed, "actual_sha256": actual_hash}


def _verify_one(
    workspace: Path,
    check: dict[str, Any],
    index: int,
    deadline: float,
    cancellation: object | None,
) -> dict[str, Any]:
    path = local_path(workspace, check["path"])
    kind = check["kind"]
    row: dict[str, Any] = {"kind": kind, "path": check["path"], "passed": False}
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("Verification budget exhausted")
    if kind == "python":
        row.update(_python_evidence(workspace, check, index, cancellation, remaining))
    else:
        row.update(_file_evidence(path, check))
    return row


def verify(workspace: Path, checks: tuple[dict[str, Any], ...], *, cancellation: object | None = None,
           timeout_seconds: float = 30) -> dict[str, Any]:
    evidence = []
    deadline = time.monotonic() + timeout_seconds
    for index, check in enumerate(checks):
        try:
            row = _verify_one(workspace, check, index, deadline, cancellation)
        except (OSError, ValueError, RuntimeError) as exc:
            row = {"kind": check.get("kind"), "path": check.get("path"), "passed": False,
                   "error": type(exc).__name__}
        evidence.append(row)
    return {"status": "unverified" if not evidence else "passed" if all(row["passed"] for row in evidence) else "failed",
            "checks": evidence}


def verdict(execution: dict[str, Any], verification: dict[str, Any]) -> str:
    if execution.get("status") in {"interrupted", "timeout", "error"}:
        return str(execution["status"])
    if execution.get("success") is not True or verification["status"] == "failed":
        return "failed"
    return "verified" if verification["status"] == "passed" else "unverified"
