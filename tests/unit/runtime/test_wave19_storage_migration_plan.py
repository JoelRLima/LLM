from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.runtime.config_repository import packaged_config_defaults
from agent.runtime.paths import AppHomeOrigin, AppPaths
from agent.runtime.storage_contracts import StorageMigrationError
from agent.runtime.storage_migration import build_migration_plan, source_profile_for


def test_explicit_profile_maps_only_allowlisted_durable_files(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "data" / "extensions").mkdir(parents=True)
    (home / "state").mkdir(parents=True)
    (home / "data" / "workspaces" / "stable-id" / "memory_backups").mkdir(parents=True)
    (home / "state" / "workspaces" / "stable-id" / "reports").mkdir(parents=True)
    (home / "config" / "config.json").write_text(json.dumps(packaged_config_defaults()), encoding="utf-8")
    (home / "data" / "extensions" / "registry.json").write_text("{}", encoding="utf-8")
    (home / "data" / "extensions" / "catalog.json.lock").write_text("lock", encoding="utf-8")
    (home / "data" / "workspaces" / "stable-id" / "agent_memory.json").write_text("{}", encoding="utf-8")
    (home / "data" / "workspaces" / "stable-id" / "memory_backups" / "copy.json").write_text("{}", encoding="utf-8")
    (home / "state" / "workspaces" / "stable-id" / "reports" / "report.txt").write_text("report", encoding="utf-8")
    (home / "state" / "workspaces" / "stable-id" / "application.lock").write_text("lock", encoding="utf-8")
    paths = AppPaths.discover(app_home=home, env={})

    profile = source_profile_for(paths)
    assert profile.name == "explicit_home"
    assert paths.home_origin is AppHomeOrigin.ARGUMENT
    plan = build_migration_plan(paths)
    relatives = {entry.relative for entry in plan.entries}
    assert "config/config.json" in relatives
    assert "global/extensions/registry.json" in relatives
    assert "workspaces/stable-id/data/agent_memory.json" in relatives
    assert "workspaces/stable-id/data/memory_backups/copy.json" in relatives
    assert "workspaces/stable-id/state/reports/report.txt" in relatives
    assert not any("lock" in item for item in relatives)
    assert plan.preserved_legacy_top_level == ("data", "state")


def test_invalid_workspace_child_fails_migration_planning(tmp_path: Path) -> None:
    home = tmp_path / "home"
    invalid = home / "data" / "workspaces" / "bad name"
    invalid.mkdir(parents=True)
    paths = AppPaths.discover(app_home=home, env={})
    with pytest.raises(StorageMigrationError) as raised:
        build_migration_plan(paths)
    assert raised.value.reason_code == "MIGRATION_SOURCE_INVALID"


def test_corrupt_durable_json_fails_before_promotion(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source = home / "data" / "workspaces" / "stable" / "agent_memory.json"
    source.parent.mkdir(parents=True)
    source.write_text("{broken", encoding="utf-8")
    paths = AppPaths.discover(app_home=home, env={})
    with pytest.raises(StorageMigrationError) as raised:
        build_migration_plan(paths)
    assert raised.value.reason_code == "MIGRATION_SOURCE_INVALID"


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlink API unavailable")
def test_source_symlink_fails_closed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source_root = home / "data" / "workspaces" / "stable"
    source_root.mkdir(parents=True)
    target = tmp_path / "outside.json"
    target.write_text("{}", encoding="utf-8")
    link = source_root / "agent_memory.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is not permitted")
    paths = AppPaths.discover(app_home=home, env={})
    with pytest.raises(StorageMigrationError) as raised:
        build_migration_plan(paths)
    assert raised.value.reason_code == "MIGRATION_SOURCE_UNSAFE"
