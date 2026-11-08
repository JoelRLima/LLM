"""Prepare persisted configuration for the managed local installation."""
from __future__ import annotations

from pathlib import Path

from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.workspace.paths import AppPaths


def prepare_installation_configuration(source: Path, home: Path) -> Path:
    """Migrate once and validate using the canonical configuration repository."""
    repository = ConfigRepository(AppPaths.discover(app_home=home))
    if not repository.path.exists():
        legacy = source / "config.json"
        if legacy.is_file():
            repository.migrate(legacy)
        else:
            repository.initialize()
    repository.load(environment={})
    config_path = repository.path
    if not isinstance(config_path, Path):
        raise TypeError("O caminho da configuracao deve ser um caminho local.")
    return config_path
