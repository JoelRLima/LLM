import subprocess
import sys
from pathlib import Path

from llm_agent.extensions.extension_catalog_lock import ExtensionCatalogLock
from tests.support.process_environment import source_child_environment

_CHILD = """
import sys
from llm_agent.extensions.extension_catalog_lock import ExtensionCatalogLock
from llm_agent.extensions.extension_catalog_errors import CatalogLockBusyError
lock = ExtensionCatalogLock(sys.argv[1])
try:
    lock.acquire()
except CatalogLockBusyError:
    print('BUSY')
else:
    print('ACQUIRED')
    lock.release()
"""


def test_same_workspace_lock_excludes_second_process(tmp_path: Path) -> None:
    path = tmp_path / "extensions.json.lock"
    lock = ExtensionCatalogLock(path)
    lock.acquire()
    try:
        result = subprocess.run(
            [sys.executable, "-c", _CHILD, str(path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
            env=source_child_environment(),
        )
    finally:
        lock.release()
    assert result.stdout.strip() == "BUSY"


def test_different_workspaces_use_independent_locks(tmp_path: Path) -> None:
    first = ExtensionCatalogLock(tmp_path / "first" / "extensions.json.lock")
    second = ExtensionCatalogLock(tmp_path / "second" / "extensions.json.lock")
    first.acquire()
    try:
        second.acquire()
        second.release()
    finally:
        first.release()
