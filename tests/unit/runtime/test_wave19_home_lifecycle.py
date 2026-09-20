from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.runtime import home_lifecycle as lifecycle_module
from agent.runtime.filesystem_primitives import WINDOWS_REPARSE_POINT, FinalPathInspection
from agent.runtime.home_lifecycle import HomeLifecycleLease, maintenance_guard
from agent.runtime.process_identity import OwnerStatus
from agent.runtime.storage_contracts import StorageMaintenanceError


class _Status:
    def __init__(self, status: OwnerStatus) -> None:
        self.status = status

    def check(self, pid: int, process_start_id: str | None) -> OwnerStatus:
        del pid, process_start_id
        return self.status


def test_startup_lease_publishes_then_activation_releases_guard(tmp_path: Path) -> None:
    home = tmp_path / "home"
    lease = HomeLifecycleLease.begin_startup(home)
    record = json.loads(lease.lease_path.read_text(encoding="utf-8"))
    assert set(record) == {"schema_version", "pid", "process_start_id", "token", "home_fingerprint", "created_at"}
    assert len(record["token"]) == 32
    lease.activate()
    with pytest.raises(StorageMaintenanceError) as raised:
        with maintenance_guard(home):
            pass
    assert raised.value.reason_code == "MAINTENANCE_HOME_ACTIVE"
    lease.close()
    assert not lease.lease_path.exists()


def test_multiple_activated_leases_can_coexist(tmp_path: Path) -> None:
    home = tmp_path / "home"
    first = HomeLifecycleLease.begin_startup(home)
    first.activate()
    second = HomeLifecycleLease.begin_startup(home)
    second.activate()
    assert first.lease_path.exists()
    assert second.lease_path.exists()
    second.close()
    first.close()


def test_dead_stale_lease_is_reaped_and_indeterminate_is_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    live = HomeLifecycleLease.begin_startup(home)
    live.activate()
    live.close()
    leases_dir = home.parent / ".home.lifecycle" / "leases"
    stale = leases_dir / "stale.json"
    stale.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pid": 999,
                "process_start_id": None,
                "token": "a" * 32,
                "home_fingerprint": __import__("hashlib").sha256(str(home.resolve()).encode()).hexdigest(),
                "created_at": "2026-09-18T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    with maintenance_guard(home, owner_liveness=_Status(OwnerStatus.DEAD)):
        pass
    assert not stale.exists()
    stale.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pid": 999,
                "process_start_id": None,
                "token": "b" * 32,
                "home_fingerprint": __import__("hashlib").sha256(str(home.resolve()).encode()).hexdigest(),
                "created_at": "2026-09-18T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(StorageMaintenanceError) as raised:
        with maintenance_guard(home, owner_liveness=_Status(OwnerStatus.INDETERMINATE)):
            pass
    assert raised.value.reason_code == "MAINTENANCE_HOME_LIVENESS_INDETERMINATE"


def test_acquire_guard_maps_open_failure_before_descriptor_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    expected = StorageMaintenanceError("MAINTENANCE_HOME_LIVENESS_INDETERMINATE", "injected")

    def fail_before_open(*_args: object, **_kwargs: object) -> int:
        raise expected

    monkeypatch.setattr(lifecycle_module, "open_verified", fail_before_open)
    with pytest.raises(StorageMaintenanceError) as raised:
        lifecycle_module._acquire_guard(home)

    assert raised.value is expected
    assert not isinstance(raised.value, UnboundLocalError)


def test_coordination_reparse_is_rejected_without_following_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    real_inspect = lifecycle_module.inspect_final_path
    metadata = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=WINDOWS_REPARSE_POINT)

    def injected_inspect(path: Path) -> FinalPathInspection:
        if Path(path).name == ".home.lifecycle":
            return FinalPathInspection(exists=True, is_link_like=True, metadata=metadata)  # type: ignore[arg-type]
        return real_inspect(path)

    monkeypatch.setattr(lifecycle_module, "inspect_final_path", injected_inspect)
    with pytest.raises(StorageMaintenanceError) as raised:
        HomeLifecycleLease.begin_startup(home)

    assert raised.value.reason_code == "MAINTENANCE_HOME_LIVENESS_INDETERMINATE"


def test_close_surfaces_owned_lease_unlink_failure_and_releases_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    lease = HomeLifecycleLease.begin_startup(home)
    real_unlink = Path.unlink

    def fail_owned_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == lease.lease_path:
            raise PermissionError("injected lease unlink failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_owned_unlink)
    with pytest.raises(StorageMaintenanceError) as raised:
        lease.close()

    assert raised.value.reason_code == "MAINTENANCE_HOME_LIVENESS_INDETERMINATE"
    assert lease._guard_descriptor is None
    descriptor = lifecycle_module._acquire_guard(home)
    lifecycle_module.unlock_descriptor(descriptor)
    lifecycle_module.os.close(descriptor)

    monkeypatch.setattr(Path, "unlink", real_unlink)
    lease.close()


def test_close_is_idempotent_when_owned_lease_disappears(tmp_path: Path) -> None:
    lease = HomeLifecycleLease.begin_startup(tmp_path / "home")
    lease.lease_path.unlink()
    lease.close()
    lease.close()
    assert lease._guard_descriptor is None


def test_temporary_unlink_failure_after_publication_returns_owned_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_unlink = Path.unlink

    def fail_temporary(path: Path, *args: object, **kwargs: object) -> None:
        if path.name.endswith(".tmp"):
            raise PermissionError("injected temporary cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_temporary)
    lease = HomeLifecycleLease.begin_startup(tmp_path / "home")
    assert lease._published and lease.lease_path.exists()
    lease.close()
    assert not lease.lease_path.exists()
    assert lease._guard_descriptor is None


def test_interrupt_during_publication_releases_guard_and_leaves_no_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupt(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(lifecycle_module.os, "link", interrupt)
    home = tmp_path / "home"
    with pytest.raises(KeyboardInterrupt):
        HomeLifecycleLease.begin_startup(home)
    leases = home.parent / ".home.lifecycle" / "leases"
    assert not tuple(leases.glob("*.json"))
    descriptor = lifecycle_module._acquire_guard(home)
    lifecycle_module.unlock_descriptor(descriptor)
    lifecycle_module.os.close(descriptor)


def test_link_then_interrupt_reconciles_owned_lease_and_releases_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_link = lifecycle_module.os.link

    def link_then_interrupt(source: Path, destination: Path) -> None:
        real_link(source, destination)
        raise KeyboardInterrupt

    monkeypatch.setattr(lifecycle_module.os, "link", link_then_interrupt)
    home = tmp_path / "home"
    with pytest.raises(KeyboardInterrupt):
        HomeLifecycleLease.begin_startup(home)
    leases = home.parent / ".home.lifecycle" / "leases"
    assert not tuple(leases.glob("*.json"))
    descriptor = lifecycle_module._acquire_guard(home)
    lifecycle_module.unlock_descriptor(descriptor)
    lifecycle_module.os.close(descriptor)


def test_startup_lease_retry_after_link_interrupt_is_not_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_link = lifecycle_module.os.link
    home = tmp_path / "home"

    with monkeypatch.context() as interrupted:
        def link_then_interrupt(source: Path, destination: Path) -> None:
            real_link(source, destination)
            raise KeyboardInterrupt

        interrupted.setattr(lifecycle_module.os, "link", link_then_interrupt)
        with pytest.raises(KeyboardInterrupt):
            HomeLifecycleLease.begin_startup(home)

    retry = HomeLifecycleLease.begin_startup(home)
    assert retry.lease_path.exists()
    retry.close()


def test_link_then_foreign_replacement_is_preserved_and_guard_is_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_link = lifecycle_module.os.link

    def link_then_interrupt(source: Path, destination: Path) -> None:
        real_link(source, destination)
        Path(destination).unlink()
        Path(destination).write_text("foreign", encoding="utf-8")
        raise KeyboardInterrupt

    monkeypatch.setattr(lifecycle_module.os, "link", link_then_interrupt)
    home = tmp_path / "home"
    with pytest.raises(StorageMaintenanceError) as raised:
        HomeLifecycleLease.begin_startup(home)
    assert raised.value.reason_code == "MAINTENANCE_HOME_LIVENESS_INDETERMINATE"
    lease_files = tuple((home.parent / ".home.lifecycle" / "leases").glob("*.json"))
    assert len(lease_files) == 1
    assert lease_files[0].read_text(encoding="utf-8") == "foreign"
    descriptor = lifecycle_module._acquire_guard(home)
    lifecycle_module.unlock_descriptor(descriptor)
    lifecycle_module.os.close(descriptor)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("home_fingerprint", "not-a-fingerprint"),
        ("pid", 0),
        ("process_start_id", 7),
        ("token", "A" * 32),
        ("created_at", "2026-09-18T00:00:00+00:00"),
    ],
)
def test_malformed_lease_is_indeterminate_and_never_reaped(
    tmp_path: Path, field: str, value: object
) -> None:
    home = tmp_path / "home"
    lease = HomeLifecycleLease.begin_startup(home)
    lease.activate()
    lease.close()
    leases_dir = home.parent / ".home.lifecycle" / "leases"
    stale = leases_dir / "malformed.json"
    record = {
        "schema_version": 1,
        "pid": 999,
        "process_start_id": None,
        "token": "a" * 32,
        "home_fingerprint": hashlib.sha256(str(home.resolve()).encode()).hexdigest(),
        "created_at": "2026-09-18T00:00:00Z",
    }
    record[field] = value
    stale.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(StorageMaintenanceError) as raised:
        with maintenance_guard(home, owner_liveness=_Status(OwnerStatus.DEAD)):
            pass

    assert raised.value.reason_code == "MAINTENANCE_HOME_LIVENESS_INDETERMINATE"
    assert stale.exists()
