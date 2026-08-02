import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext
from agent.tools.extension_catalog_errors import CatalogLockBusyError
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.workspace_extensions_service import WorkspaceExtensionService

_CHILD = """
import sys
from pathlib import Path
from agent.runtime.paths import AppPaths
from agent.tools.extension_catalog_errors import CatalogLockBusyError
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.workspace_extensions_service import WorkspaceExtensionService

workspace_id, catalog_path, action = sys.argv[1:]
app_home = Path(catalog_path).parents[2]
paths = AppPaths.discover(app_home, env={})
catalog = ExtensionCatalogService(ExtensionCatalogStorage(catalog_path))
service = WorkspaceExtensionService.for_workspace(paths, workspace_id, catalog)
try:
    if action == 'enable': service.enable('demo.extension')
    elif action == 'disable': service.disable('demo.extension')
    elif action == 'grant': service.grant('demo.extension', 'read')
    elif action == 'revoke': service.revoke('demo.extension', 'read')
    else: service.remove_configuration('demo.extension')
except CatalogLockBusyError:
    print('BUSY')
"""

_PAUSING_CHILD = """
import sys
import time
from pathlib import Path
from agent.runtime.paths import AppPaths
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.workspace_extensions_service import WorkspaceExtensionService
from agent.tools.workspace_extensions_storage import WorkspaceExtensionsStorage

workspace_id, catalog_path, action, ready_path, release_path = sys.argv[1:]
app_paths = AppPaths.discover(Path(catalog_path).parents[2], env={})
catalog = ExtensionCatalogService(ExtensionCatalogStorage(catalog_path))
canonical = app_paths.for_workspace(workspace_id)

class PausingStorage(WorkspaceExtensionsStorage):
    def __init__(self, path, ready, release):
        super().__init__(path)
        self.ready = Path(ready)
        self.release = Path(release)

    def save(self, state):
        self.ready.write_text('ready', encoding='utf-8')
        deadline = time.monotonic() + 15
        while not self.release.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError('release signal not received')
            time.sleep(0.01)
        super().save(state)

storage = PausingStorage(canonical.workspace_extensions_file, ready_path, release_path)
service = WorkspaceExtensionService._for_testing(
    app_paths, workspace_id, catalog, storage=storage
)
if action == 'enable':
    service.enable('demo.extension')
elif action == 'grant':
    service.grant('demo.extension', 'read')
elif action == 'remove':
    service.remove_configuration('demo.extension')
"""


def _manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "id": "demo.extension",
                "version": "1",
                "protocol_version": "1.0",
                "transport": "stdio",
                "entrypoint": ["python", "demo.py"],
                "timeout_seconds": 5,
                "tools": [{"name": "tool", "schema": {}, "capabilities": ["read"]}],
            }
        ),
        encoding="utf-8",
    )


def _wait_for_signal(path: Path) -> None:
    deadline = time.monotonic() + 15
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"signal not received: {path}")
        time.sleep(0.01)


def _start_paused_writer(
    paths: AppPaths,
    workspace_id: str,
    action: str,
    tmp_path: Path,
) -> tuple[subprocess.Popen[str], Path, Path]:
    ready = tmp_path / f"{action}.ready"
    release = tmp_path / f"{action}.release"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _PAUSING_CHILD,
            workspace_id,
            str(paths.extensions_catalog_file),
            action,
            str(ready),
            str(release),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _wait_for_signal(ready)
    return process, ready, release


def _finish_paused_writer(process: subprocess.Popen[str], release: Path) -> None:
    release.write_text("release", encoding="utf-8")
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0, (stdout, stderr)


@pytest.mark.parametrize("action", ["enable", "disable", "grant", "revoke", "remove"])
def test_mutations_are_excluded_while_same_workspace_lock_is_held(
    tmp_path: Path, action: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = tmp_path / "manifest.json"
    _manifest(manifest)
    paths = AppPaths.discover(tmp_path / "app", env={})
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    catalog.add(manifest)
    workspace_paths = paths.for_workspace(WorkspaceContext.create(workspace).workspace_id)
    service = WorkspaceExtensionService.for_workspace(
        paths, workspace_paths.workspace_id, catalog
    )
    if action in {"disable", "grant", "revoke", "remove"}:
        service.enable("demo.extension")
    if action == "revoke":
        service.grant("demo.extension", "read")

    service.lock.acquire()
    try:
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                _CHILD,
                workspace_paths.workspace_id,
                str(paths.extensions_catalog_file),
                action,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
    finally:
        service.lock.release()
    assert child.stdout.strip() == "BUSY"
    # The caller explicitly retries after the non-blocking lock is released;
    # the operation must reload the promoted state before applying its change.
    if action == "enable":
        assert service.enable("demo.extension").changed is True
    elif action == "disable":
        assert service.disable("demo.extension").changed is True
    elif action == "grant":
        assert service.grant("demo.extension", "read").changed is True
    elif action == "revoke":
        assert service.revoke("demo.extension", "read").changed is True
    else:
        assert service.remove_configuration("demo.extension").changed is True


def test_enable_then_disable_is_serial_after_real_save_window(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = tmp_path / "manifest.json"
    _manifest(manifest)
    paths = AppPaths.discover(tmp_path / "app", env={})
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    catalog.add(manifest)
    workspace_id = WorkspaceContext.create(workspace).workspace_id
    writer, _, release = _start_paused_writer(paths, workspace_id, "enable", tmp_path)
    service = WorkspaceExtensionService.for_workspace(paths, workspace_id, catalog)
    try:
        with pytest.raises(CatalogLockBusyError):
            service.disable("demo.extension")
    finally:
        _finish_paused_writer(writer, release)
    result = service.disable("demo.extension")
    assert result.changed is True
    assert service.load().get("demo.extension").enabled is False
    assert b'"enabled": false' in service.workspace_paths.workspace_extensions_file.read_bytes()


def test_grant_then_revoke_is_serial_after_real_save_window(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = tmp_path / "manifest.json"
    _manifest(manifest)
    paths = AppPaths.discover(tmp_path / "app", env={})
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    catalog.add(manifest)
    workspace_id = WorkspaceContext.create(workspace).workspace_id
    service = WorkspaceExtensionService.for_workspace(paths, workspace_id, catalog)
    service.enable("demo.extension")
    writer, _, release = _start_paused_writer(paths, workspace_id, "grant", tmp_path)
    try:
        with pytest.raises(CatalogLockBusyError):
            service.revoke("demo.extension", "read")
    finally:
        _finish_paused_writer(writer, release)
    result = service.revoke("demo.extension", "read")
    assert result.changed is True
    assert service.load().get("demo.extension").granted_capabilities == ()


def test_remove_then_enable_is_serial_after_real_save_window(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manifest = tmp_path / "manifest.json"
    _manifest(manifest)
    paths = AppPaths.discover(tmp_path / "app", env={})
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    catalog.add(manifest)
    workspace_id = WorkspaceContext.create(workspace).workspace_id
    service = WorkspaceExtensionService.for_workspace(paths, workspace_id, catalog)
    service.enable("demo.extension")
    service.grant("demo.extension", "read")
    writer, _, release = _start_paused_writer(paths, workspace_id, "remove", tmp_path)
    try:
        with pytest.raises(CatalogLockBusyError):
            service.enable("demo.extension")
    finally:
        _finish_paused_writer(writer, release)
    result = service.enable("demo.extension")
    assert result.changed is True
    assert service.load().get("demo.extension").enabled is True
    assert service.load().get("demo.extension").granted_capabilities == ()


def test_different_workspaces_complete_while_one_save_is_paused(tmp_path: Path) -> None:
    first_root = tmp_path / "workspace-a"
    second_root = tmp_path / "workspace-b"
    first_root.mkdir()
    second_root.mkdir()
    manifest = tmp_path / "manifest.json"
    _manifest(manifest)
    paths = AppPaths.discover(tmp_path / "app", env={})
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    catalog.add(manifest)
    first_id = WorkspaceContext.create(first_root).workspace_id
    second_id = WorkspaceContext.create(second_root).workspace_id
    writer, _, release = _start_paused_writer(paths, first_id, "enable", tmp_path)
    second = WorkspaceExtensionService.for_workspace(paths, second_id, catalog)
    try:
        assert second.enable("demo.extension").changed is True
        assert second.load().get("demo.extension").enabled is True
    finally:
        _finish_paused_writer(writer, release)
    first_file = paths.for_workspace(first_id).workspace_extensions_file
    second_file = paths.for_workspace(second_id).workspace_extensions_file
    assert first_file != second_file
    assert first_file.read_bytes() != b""
    assert second_file.read_bytes() != b""
