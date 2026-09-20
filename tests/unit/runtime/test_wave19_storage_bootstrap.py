from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.runtime.paths import AppPaths
from agent.runtime.storage_bootstrap import StorageBootstrap
from agent.runtime.storage_contracts import StorageLayoutError, StorageLayoutStatus


def test_probe_distinguishes_fresh_and_canonical(tmp_path: Path) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    bootstrap = StorageBootstrap()
    assert bootstrap.probe(paths) is StorageLayoutStatus.FRESH
    bootstrap.prepare(paths)
    assert bootstrap.probe(paths) is StorageLayoutStatus.CANONICAL


def test_invalid_marker_is_not_replaced(tmp_path: Path) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    paths.global_dir.mkdir(parents=True)
    paths.storage_layout_file.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
    with pytest.raises(StorageLayoutError) as raised:
        StorageBootstrap().prepare(paths)
    assert raised.value.reason_code == "MIGRATION_LAYOUT_MARKER_INVALID"
    assert json.loads(paths.storage_layout_file.read_text(encoding="utf-8"))["schema_version"] == 999
