from __future__ import annotations

from pathlib import Path

from agent.interfaces.cli.maintenance import initialize_config
from agent.runtime.paths import AppPaths


def test_standalone_config_init_bootstraps_and_closes_transient_lease(tmp_path: Path) -> None:
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    created = initialize_config(paths, None)
    assert created == paths.config_file
    assert paths.storage_layout_file.exists()
    leases = paths.home_dir.parent / ".home.lifecycle" / "leases"
    assert not tuple(leases.glob("*.json"))
