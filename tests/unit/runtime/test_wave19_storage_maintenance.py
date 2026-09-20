from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.runtime import storage_maintenance as maintenance_module
from agent.runtime.filesystem_primitives import WINDOWS_REPARSE_POINT, FinalPathInspection
from agent.runtime.paths import AppPaths
from agent.runtime.storage_bootstrap import StorageBootstrap
from agent.runtime.storage_contracts import (
    MaintenanceConfirmation,
    MaintenanceOperation,
    StorageMaintenanceError,
)
from agent.runtime.storage_maintenance import StorageMaintenanceService


def _canonical(tmp_path: Path) -> tuple[AppPaths, StorageMaintenanceService]:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    StorageBootstrap().prepare(paths)
    return paths, StorageMaintenanceService(paths)


def test_reset_archives_and_restore_never_overwrites(tmp_path: Path) -> None:
    paths, service = _canonical(tmp_path)
    (paths.home_dir / "global" / "owned.txt").write_text("owned", encoding="utf-8")
    reset = service.reset(MaintenanceConfirmation(MaintenanceOperation.RESET, str(paths.home_dir)))
    assert not paths.home_dir.exists()
    assert reset.path.is_dir()
    restored = service.restore(
        reset.backup_id,
        MaintenanceConfirmation(MaintenanceOperation.RESTORE, str(paths.home_dir), reset.backup_id),
    )
    assert restored.restored_home == paths.home_dir
    assert (paths.home_dir / "global" / "owned.txt").read_text(encoding="utf-8") == "owned"


def test_foreign_top_level_fails_closed_before_rename(tmp_path: Path) -> None:
    paths, service = _canonical(tmp_path)
    foreign = paths.home_dir / "install"
    foreign.mkdir()
    with pytest.raises(StorageMaintenanceError) as raised:
        service.reset(MaintenanceConfirmation(MaintenanceOperation.RESET, str(paths.home_dir)))
    assert raised.value.reason_code == "MAINTENANCE_FOREIGN_ENTRY"
    assert paths.home_dir.exists()


def test_confirmation_and_restore_target_are_typed_and_explicit(tmp_path: Path) -> None:
    paths, service = _canonical(tmp_path)
    with pytest.raises(StorageMaintenanceError) as raised:
        service.reset(MaintenanceConfirmation(MaintenanceOperation.RESTORE, str(paths.home_dir)))
    assert raised.value.reason_code == "MAINTENANCE_CONFIRMATION_MISMATCH"


def test_backup_with_valid_name_but_reparse_implementation_is_not_inspected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, service = _canonical(tmp_path)
    backup_id = "home.backup.20260918T144742050404Z.abcdef12"
    reparse = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=WINDOWS_REPARSE_POINT)
    real_inspect = maintenance_module.inspect_final_path

    def injected_inspect(path: Path):
        if Path(path).name == backup_id:
            return FinalPathInspection(exists=True, is_link_like=True, metadata=reparse)  # type: ignore[arg-type]
        return real_inspect(path)

    monkeypatch.setattr(maintenance_module, "inspect_final_path", injected_inspect)
    with pytest.raises(StorageMaintenanceError) as raised:
        service.inspect_backup(backup_id)

    assert raised.value.reason_code == "MAINTENANCE_BACKUP_NOT_FOUND"


def test_canonical_top_level_reparse_is_rejected_before_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, service = _canonical(tmp_path)
    reparse = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=WINDOWS_REPARSE_POINT)
    real_inspect = maintenance_module.inspect_final_path

    def injected_inspect(path: Path):
        if Path(path) == paths.global_dir:
            return FinalPathInspection(exists=True, is_link_like=True, metadata=reparse)  # type: ignore[arg-type]
        return real_inspect(path)

    monkeypatch.setattr(maintenance_module, "inspect_final_path", injected_inspect)
    with pytest.raises(StorageMaintenanceError) as raised:
        service.reset(MaintenanceConfirmation(MaintenanceOperation.RESET, str(paths.home_dir)))

    assert raised.value.reason_code == "MAINTENANCE_FOREIGN_ENTRY"
    assert paths.home_dir.exists()
