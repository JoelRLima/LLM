"""Finite Application operations for configuration administration."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.agent.runtime.home_lifecycle import HomeLifecycleLease
from llm_agent.agent.runtime.storage_bootstrap import StorageBootstrap
from llm_agent.application.configuration_errors import translate_configuration_errors
from llm_agent.application.context import AppPaths

if TYPE_CHECKING:
    class _ConfigRepositoryView(Protocol):
        """Typed view of the canonical owner across mypy's skipped import."""

        @property
        def path(self) -> Path: ...

        def load(self, *, overrides: Mapping[str, str] | None) -> object: ...

        def initialize(self) -> Path: ...

        def migrate(self, source: str | Path) -> Path: ...


def _repository(
    app_paths: AppPaths,
    config_path: str | Path | None,
) -> _ConfigRepositoryView:
    return cast("_ConfigRepositoryView", ConfigRepository(app_paths, config_path=config_path))


def configuration_path(
    app_paths: AppPaths,
    config_path: str | Path | None = None,
) -> Path:
    with translate_configuration_errors():
        repository = _repository(app_paths, config_path)
        return repository.path


def validate_configuration(
    app_paths: AppPaths,
    config_path: str | Path | None = None,
    profile: str | None = None,
) -> Path:
    with translate_configuration_errors():
        repository = _repository(app_paths, config_path)
        repository.load(
            overrides={"default_model_profile": profile} if profile is not None else None
        )
        return repository.path


def initialize_configuration(
    app_paths: AppPaths,
    config_path: str | Path | None = None,
) -> Path:
    with translate_configuration_errors():
        repository = _repository(app_paths, config_path)
        target = repository.path.resolve()
        home = app_paths.home_dir.resolve()
        try:
            target.relative_to(home)
        except ValueError:
            return repository.initialize()

        lease = HomeLifecycleLease.begin_transient(home)
        try:
            StorageBootstrap().prepare(app_paths)
            return repository.initialize()
        finally:
            lease.close()


def migrate_configuration(
    app_paths: AppPaths,
    source: str | Path,
    *,
    config_path: str | Path | None = None,
) -> Path:
    with translate_configuration_errors():
        repository = _repository(app_paths, config_path)
        target = repository.path.resolve()
        home = app_paths.home_dir.resolve()
        try:
            target.relative_to(home)
        except ValueError:
            return repository.migrate(source)

        lease = HomeLifecycleLease.begin_transient(home)
        try:
            StorageBootstrap().prepare(app_paths)
            return repository.migrate(source)
        finally:
            lease.close()


__all__ = [
    "configuration_path",
    "validate_configuration",
    "initialize_configuration",
    "migrate_configuration",
]
