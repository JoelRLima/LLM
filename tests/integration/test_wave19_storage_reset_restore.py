from __future__ import annotations

from pathlib import Path

from agent.runtime.paths import AppPaths
from agent.runtime.storage_bootstrap import StorageBootstrap
from agent.runtime.storage_contracts import MaintenanceConfirmation, MaintenanceOperation
from agent.runtime.storage_maintenance import StorageMaintenanceService


def test_integrated_reset_restore_preserves_canonical_home_contents(tmp_path: Path) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    StorageBootstrap().prepare(paths)
    marker = paths.storage_layout_file.read_bytes()
    service = StorageMaintenanceService(paths)
    backup = service.reset(MaintenanceConfirmation(MaintenanceOperation.RESET, str(paths.home_dir)))
    service.restore(
        backup.backup_id,
        MaintenanceConfirmation(MaintenanceOperation.RESTORE, str(paths.home_dir), backup.backup_id),
    )
    assert paths.storage_layout_file.read_bytes() == marker
