from __future__ import annotations

from pathlib import Path

from agent.application import AgentApplication
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.paths import AppPaths


def test_application_owns_and_releases_home_lease(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AppPaths.discover(app_home=tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    application = AgentApplication.create(paths=paths, workspace=workspace, configure_logging=False)
    lease_path = application._home_lease.lease_path
    assert lease_path.exists()
    application.close()
    assert not lease_path.exists()
