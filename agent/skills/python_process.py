"""Cooperative process lifecycle for the validated Python skill."""

from __future__ import annotations

import os
import subprocess
import time
from threading import Event
from typing import Any, Callable

from agent.cancellation import is_cancellation_requested
from agent.tools.process_tree import process_group_id, terminate_process


class PythonProcessCancelled(Exception):
    """Raised after the sandbox process tree has been interrupted."""


def run_python_process(
    command: list[str], *, cwd: str, timeout: int,
    cancellation_token: Any | None, cancellation_event: Event | None,
    drop_privileges: Callable[[], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        cwd=cwd,
        shell=False,
        start_new_session=os.name != "nt",
        creationflags=(int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0),
        preexec_fn=drop_privileges if os.name == "posix" and drop_privileges is not None else None,
    )
    group = process_group_id(process)
    deadline = time.monotonic() + timeout
    try:
        while True:
            if is_cancellation_requested(cancellation_token, cancellation_event):
                terminate_process(process, process_group_id=group)
                process.communicate()
                raise PythonProcessCancelled
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                terminate_process(process, process_group_id=group)
                process.communicate()
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                stdout, stderr = process.communicate(timeout=min(0.05, remaining))
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            terminate_process(process, process_group_id=group)
        process.communicate()


__all__ = ["PythonProcessCancelled", "run_python_process"]
