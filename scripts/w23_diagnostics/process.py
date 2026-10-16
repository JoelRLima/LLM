"""Serial commands with native containment and redacted, durable evidence."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.w23_diagnostics.capture import Capture
from scripts.w23_diagnostics.evidence import Events, Redactor, write_json
from scripts.w23_diagnostics.native import NativeChild, OwnedJob

SAFE_ENV = frozenset({
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "LOCALAPPDATA", "APPDATA", "USERPROFILE",
    "USERNAME", "USERDOMAIN", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "PROGRAMDATA",
    "ALLUSERSPROFILE", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "PYTHONUTF8", "PYTHONIOENCODING", "GITHUB_ACTIONS", "RUNNER_ENVIRONMENT",
    "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_JOB",
})


def environment(scratch: Path, source: Path) -> dict[str, str]:
    scratch.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if key.upper() in SAFE_ENV}
    env.update(TEMP=str(scratch), TMP=str(scratch), TMPDIR=str(scratch),
               PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1",
               PYTHONPATH=str(source) + os.pathsep + str(source / "src"), PYTHONHASHSEED="0",
               GIT_OPTIONAL_LOCKS="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               PIP_CACHE_DIR=str(scratch / "pip"), RUFF_CACHE_DIR=str(scratch / "ruff"),
               MYPY_CACHE_DIR=str(scratch / "mypy"))
    return env


@dataclass
class Result:
    status: str
    code: int | None
    stdout: str = ""  # Bounded raw memory for verifier semantics; never serialized.
    stderr: str = ""


class Commands:
    def __init__(self, output: Path, redactor: Redactor) -> None:
        self.output, self.redactor = output, redactor
        output.mkdir(parents=True, exist_ok=True)
        self.events = Events(output / "processes.jsonl", redactor)
        self.sequence = 0
        self.deadline = time.monotonic() + 1800

    def run(self, label: str, command: list[str], cwd: Path, env: dict[str, str], seconds: int) -> Result:
        self.sequence += 1
        name = f"{self.sequence:03d}-{label}"
        seconds = min(seconds, max(0, int(self.deadline - time.monotonic())))
        record: dict[str, Any] = {"status": "UNKNOWN", "command": self.redactor.value(command),
                                  "timeout_seconds": seconds, "label": label}
        path = self.output / (name + ".json")
        write_json(path, record)
        started = time.monotonic()
        try:
            result = self._contained(name, command, cwd, env, seconds, record) if seconds else Result("TIMEOUT", None)
        except BaseException as exc:
            result = Result("UNKNOWN", None)
            record["exception_type"] = type(exc).__name__
        record.update(status=result.status, returncode=result.code, wall_seconds=time.monotonic() - started)
        write_json(path, record)
        return result

    def _contained(self, name: str, command: list[str], cwd: Path, env: dict[str, str],
                   seconds: int, record: dict[str, Any]) -> Result:
        job = OwnedJob()
        child: NativeChild | None = None
        captures: list[Capture] = []
        try:
            with open(os.devnull, "rb") as stdin:
                child, captures = self._start(job, name, command, cwd, env, stdin)
            record.update(pid=child.pid, primary_thread_id=child.tid, ownership="assigned-before-resume",
                          job_memory_mb=4096, process_memory_mb=3072, max_processes=48)
            state, code = self._wait(child, job, captures, seconds)
            record["root_cpu"] = child.cpu()
            remaining = job.pids()
            if remaining:
                job.terminate(125)
                if state == "PASS":
                    state = "UNKNOWN"
            record["residual_owned_pids"] = remaining
        finally:
            try:
                job.close()  # KILL_ON_JOB_CLOSE, owner is outside this job.
            finally:
                try:
                    if child is not None:
                        child.close()
                finally:
                    for capture in captures:
                        capture.thread.join(timeout=5)
        complete = all(not capture.thread.is_alive() and not capture.error for capture in captures)
        record["capture_complete"] = complete
        if not complete or any(capture.limited for capture in captures):
            state = "UNKNOWN" if state != "TIMEOUT" else state
        return Result(state, code, captures[0].text(), captures[1].text())

    def _start(self, job: OwnedJob, name: str, command: list[str], cwd: Path, env: dict[str, str],
               stdin: Any) -> tuple[NativeChild, list[Capture]]:
        pipes = [os.pipe(), os.pipe()]
        captures = [Capture(pair[0], self.output / f"{name}.{suffix}.log", self.redactor)
                    for pair, suffix in zip(pipes, ("stdout", "stderr"), strict=True)]
        try:
            child = NativeChild(job, command, cwd, env, pipes[0][1], pipes[1][1], stdin)
        except BaseException:
            for capture in captures:
                os.close(capture.fd)
            raise
        finally:
            for _reader, writer in pipes:
                os.close(writer)
        for capture in captures:
            capture.thread.start()
        self.events.emit("child_started", pid_owned=child.pid, thread_id=child.tid, label=name)
        return child, captures

    def _wait(self, child: NativeChild, job: OwnedJob, captures: list[Capture],
              seconds: int) -> tuple[str, int | None]:
        start, next_snapshot = time.monotonic(), 0.0
        while True:
            code = child.poll()
            if code is not None:
                return ("PASS" if code == 0 else "FAIL"), code
            elapsed = time.monotonic() - start
            if elapsed >= seconds:
                self.events.emit("command_timeout", pid_owned=child.pid)
                job.terminate(124)
                return "TIMEOUT", None
            if elapsed >= next_snapshot:
                self.events.emit("owned_job_snapshot", pids=job.pids(), root_cpu=child.cpu())
                next_snapshot = elapsed + 15
            if any(capture.error or capture.limited for capture in captures):
                job.terminate(125)
                return "UNKNOWN", None
            time.sleep(0.1)


def require(result: Result) -> None:
    if result.status != "PASS":
        raise StageError(result.status)


class StageError(RuntimeError):
    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status
