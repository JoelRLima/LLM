"""Explicit detached process launch with file output and no inherited console."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Mapping, Sequence

from llm_agent.filesystem.lock_filesystem import open_verified
from llm_agent.filesystem.path_safety import assert_no_link_ancestors


def launch_detached(argv: Sequence[str], *, cwd: Path, output: Path, env: Mapping[str, str]) -> int:
    assert_no_link_ancestors(output)
    descriptor = open_verified(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(descriptor, "wb") as log:
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        child = subprocess.Popen(list(argv), cwd=cwd, env=dict(env), stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, close_fds=True,
                                 creationflags=flags, start_new_session=os.name != "nt")
    # Reap if the parent stays alive. This thread does not keep the UI alive.
    threading.Thread(target=child.wait, name="detached-process-reaper", daemon=True).start()
    return child.pid
