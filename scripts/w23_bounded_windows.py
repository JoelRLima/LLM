"""Serial bounded Windows acceptance runner; never reuse scratch or evidence."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    from scripts.w23_windows_job import (
        CREATE_NEW_PROCESS_GROUP,
        CREATE_NO_WINDOW,
        _create_limited_job,
        _job_counts,
        _terminate_campaign_tree,
        _winerror,
    )
except ModuleNotFoundError:
    from w23_windows_job import (  # type: ignore[no-redef]
        CREATE_NEW_PROCESS_GROUP,
        CREATE_NO_WINDOW,
        _create_limited_job,
        _job_counts,
        _terminate_campaign_tree,
        _winerror,
    )

def _save(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(pending, path)


def _environment(scratch: Path) -> dict[str, str]:
    env = os.environ.copy()
    temp = scratch / "temp"
    for directory in (temp, scratch / "ruff-cache", scratch / "mypy-cache"):
        directory.mkdir(parents=True, exist_ok=True)
    env.pop("PYTHONPYCACHEPREFIX", None)
    env.update({
        "TEMP": str(temp),
        "TMP": str(temp),
        "TMPDIR": str(temp),
        "PYTHONDONTWRITEBYTECODE": "1",
        "RUFF_CACHE_DIR": str(scratch / "ruff-cache"),
        "MYPY_CACHE_DIR": str(scratch / "mypy-cache"),
        "GIT_OPTIONAL_LOCKS": "0",
        "PYTHONHASHSEED": "0",
    })
    return env


def _command(args: argparse.Namespace) -> list[str]:
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if args.self_test:
        command = [sys.executable, "-c", "print('job-object-child-ok')"]
    if not command:
        raise ValueError("no child command supplied")
    return command


def _approved_root(local_appdata: Path) -> Path:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return (Path(os.environ["RUNNER_TEMP"]) / "w23-private").resolve(strict=True)
    return (local_appdata / "W23_CAND03_S5_TEMP_20261014").resolve(strict=True)


def _paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path, Path, Path]:
    if os.name != "nt":
        raise RuntimeError("this bounded runner requires native Windows")
    evidence = Path(args.evidence_directory).resolve(strict=True)
    working = Path(args.working_directory).resolve(strict=True)
    if not working.is_dir() or not evidence.is_dir():
        raise ValueError("working/evidence directory is not a directory")
    local_appdata = Path(os.environ["LOCALAPPDATA"]).resolve(strict=True)
    scratch_root = Path(args.scratch_root).resolve(strict=True)
    expected_root = _approved_root(local_appdata)
    if os.path.normcase(str(scratch_root)) != os.path.normcase(str(expected_root)):
        raise ValueError("scratch root is not the preapproved S5 local-profile root")
    if not scratch_root.is_dir() or scratch_root.is_symlink():
        raise ValueError("S5 scratch root is unavailable or a symlink")
    if getattr(os.lstat(scratch_root), "st_file_attributes", 0) & 0x400:
        raise ValueError("S5 scratch root is a reparse point")
    scratch = Path(args.scratch_directory).resolve(strict=False)
    basetemp = Path(args.pytest_basetemp).resolve(strict=False)
    if os.path.normcase(str(scratch.parent)) != os.path.normcase(str(scratch_root)):
        raise ValueError("scratch must be a direct child of the preapproved S5 root")
    if os.path.normcase(str(basetemp.parent)) != os.path.normcase(str(scratch)):
        raise ValueError("pytest basetemp must be a direct child of this run's scratch")
    if scratch.exists() or basetemp.exists():
        raise FileExistsError("runner refuses to reuse any scratch directory")
    output = evidence / args.stdout_log
    result_path = evidence / args.result_json
    if output.exists() or result_path.exists():
        raise FileExistsError("runner refuses to overwrite prior logs")
    return evidence, working, output, result_path, scratch, basetemp


def _initial_record(args: argparse.Namespace, command: list[str], working: Path) -> dict[str, Any]:
    return {
        "status": "RUNNING",
        "command": command,
        "working_directory": str(working),
        "timeout_seconds": args.timeout_seconds,
        "job_memory_limit_bytes": args.job_memory_mb * 1024 * 1024,
        "per_process_memory_limit_bytes": args.process_memory_mb * 1024 * 1024,
        "active_process_limit": args.max_processes,
        "runner_pid": os.getpid(),
    }


def _start_child(
    command: list[str],
    working: Path,
    env: dict[str, str],
    stream: Any,
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        cwd=working,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=stream,
        stderr=subprocess.STDOUT,
        close_fds=True,
        creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
    )


def _wait_child(
    child: subprocess.Popen[bytes],
    args: argparse.Namespace,
    started: float,
    kernel: Any,
    job: Any,
    result_path: Path,
    record: dict[str, Any],
) -> int | None:
    while child.poll() is None:
        if time.monotonic() - started >= args.timeout_seconds:
            record["status"] = "TIMEOUT"
            record["return_code"] = None
            _save(result_path, record)
            _terminate_campaign_tree(kernel, job, child)
            return None
        time.sleep(0.5)
    return int(child.returncode or 0)


def _finish_child(
    return_code: int,
    kernel: Any,
    job: Any,
    result_path: Path,
    record: dict[str, Any],
) -> int:
    active, peak_memory = _job_counts(kernel, job)
    record["return_code"] = return_code
    record["peak_job_memory_bytes"] = peak_memory
    record["active_processes_after_child_exit"] = active
    passed = return_code == 0 and active == 1
    record["status"] = "OBSERVED PASS" if passed else "OBSERVED FAIL"
    _save(result_path, record)
    if active > 1 and not kernel.TerminateJobObject(job, 125):
        raise _winerror("TerminateJobObject residual children failed")
    return 0 if passed else (return_code or 1)


def run(args: argparse.Namespace) -> int:
    command = _command(args)
    evidence, working, output, result_path, scratch, basetemp = _paths(args)
    if not args.self_test and args.pytest_command:
        command.extend(["--basetemp", str(basetemp)])
    env = _environment(scratch)
    kernel, job = _create_limited_job(args.job_memory_mb, args.process_memory_mb, args.max_processes)
    record = _initial_record(args, command, working)
    record["scratch_directory"] = str(scratch)
    record["pytest_basetemp"] = str(basetemp)
    child: subprocess.Popen[bytes] | None = None
    _save(result_path, record)
    try:
        with output.open("xb") as stream:
            child = _start_child(command, working, env, stream)
            record["child_pid"] = child.pid
            _save(result_path, record)
            return_code = _wait_child(
                child, args, time.monotonic(), kernel, job, result_path, record,
            )
        if return_code is None:
            return 124
        return _finish_child(return_code, kernel, job, result_path, record)
    except BaseException as exc:
        record["status"] = "OBSERVED FAIL"
        record["exception_type"] = type(exc).__name__
        _save(result_path, record)
        if child is not None and child.poll() is None:
            _terminate_campaign_tree(kernel, job, child)
        raise
    finally:
        kernel.CloseHandle(job)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--working-directory", required=True)
    parser.add_argument("--evidence-directory", required=True)
    parser.add_argument("--scratch-root", required=True)
    parser.add_argument("--scratch-directory", required=True)
    parser.add_argument("--pytest-basetemp", required=True)
    parser.add_argument("--stdout-log", required=True)
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--job-memory-mb", type=int, default=4096)
    parser.add_argument("--process-memory-mb", type=int, default=3072)
    parser.add_argument("--max-processes", type=int, default=64)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--pytest-command", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        return run(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"runner blocked or failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 125


if __name__ == "__main__":
    raise SystemExit(main())
