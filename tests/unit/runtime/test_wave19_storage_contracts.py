from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.runtime import storage_contracts as contracts_module
from agent.runtime.filesystem_primitives import WINDOWS_REPARSE_POINT, FinalPathInspection
from agent.runtime.storage_contracts import (
    LAYOUT_ID,
    LAYOUT_VERSION,
    StorageLayoutError,
    StorageMigrationError,
    backup_id_is_valid,
    make_migration_receipt,
    read_layout_marker,
    read_migration_receipt,
    validate_migration_receipt,
    write_layout_marker,
    write_migration_receipt,
)


def test_layout_marker_is_exact_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "global" / "storage_layout.json"
    path.parent.mkdir()
    assert write_layout_marker(path) == {
        "schema_version": 1,
        "layout_id": LAYOUT_ID,
        "layout_version": LAYOUT_VERSION,
    }
    assert read_layout_marker(path) == {
        "schema_version": 1,
        "layout_id": LAYOUT_ID,
        "layout_version": LAYOUT_VERSION,
    }


def test_layout_marker_rejects_extra_and_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "marker.json"
    path.write_text('{"schema_version":1,"layout_id":"w19-single-root-v1","layout_version":19,"x":1}', encoding="utf-8")
    with pytest.raises(StorageLayoutError):
        read_layout_marker(path)
    path.write_text('{"schema_version":1,"schema_version":1,"layout_id":"w19-single-root-v1","layout_version":19}', encoding="utf-8")
    with pytest.raises(StorageLayoutError):
        read_layout_marker(path)


def test_migration_receipt_is_path_minimized_and_profile_bounded() -> None:
    receipt = make_migration_receipt(
        source_profile="explicit_home",
        copied=["workspaces/id/data/agent_memory.json"],
        identical=[],
        preserved_legacy_top_level=["data"],
    )
    assert validate_migration_receipt(receipt) == receipt
    with pytest.raises(StorageMigrationError):
        validate_migration_receipt({**receipt, "preserved_legacy_top_level": ["foreign"]})
    with pytest.raises(StorageMigrationError):
        validate_migration_receipt({**receipt, "copied": ["../outside"]})


def test_migration_receipt_round_trips_atomically(tmp_path: Path) -> None:
    path = tmp_path / "global" / "migrations" / "w18_to_w19_v1.json"
    receipt = make_migration_receipt(
        source_profile="windows_default",
        copied=[],
        identical=["config/config.json"],
        preserved_legacy_top_level=[],
    )
    assert write_migration_receipt(path, receipt) == read_migration_receipt(path)
    assert json.loads(path.read_text(encoding="utf-8")) == receipt


def test_backup_id_grammar_is_exact() -> None:
    valid = "home.backup.20260918T144742050404Z.abcdef12"
    assert backup_id_is_valid(valid, "home")
    assert not backup_id_is_valid(valid.upper(), "home")
    assert not backup_id_is_valid("home.backup.20260918T144742Z.abcdef12", "home")


def test_layout_marker_reparse_final_file_is_rejected_without_following_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "global" / "storage_layout.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"schema_version": 1, "layout_id": LAYOUT_ID, "layout_version": LAYOUT_VERSION}), encoding="utf-8")
    metadata = type("ReparseStat", (), {"st_mode": 0o100644, "st_file_attributes": WINDOWS_REPARSE_POINT})()
    monkeypatch.setattr(
        contracts_module,
        "inspect_final_path",
        lambda _path: FinalPathInspection(exists=True, is_link_like=True, metadata=metadata),  # type: ignore[arg-type]
    )

    with pytest.raises(StorageLayoutError) as raised:
        read_layout_marker(path)

    assert raised.value.reason_code == "MIGRATION_LAYOUT_MARKER_INVALID"
