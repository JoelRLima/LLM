from __future__ import annotations

import json
from pathlib import Path

from agent.runtime.paths import AppPaths
from agent.runtime.storage_bootstrap import StorageBootstrap


def test_integrated_explicit_home_migration_is_source_preserving(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "state").mkdir()
    (home / "config" / "config.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    (home / "state" / "last_workspace.json").write_text("{}", encoding="utf-8")
    paths = AppPaths.discover(app_home=home, env={})

    result = StorageBootstrap().prepare(paths)

    assert result.migrated is True
    assert paths.last_workspace_file.exists()
    assert (home / "state" / "last_workspace.json").exists()
