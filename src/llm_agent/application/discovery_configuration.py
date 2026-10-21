"""Optional, side-effect-free config resolution for semantic Discovery."""

from __future__ import annotations

from pathlib import Path

from llm_agent.agent.llm.model_profile import ResolvedModelProfile
from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.application.context import AppPaths


def resolve_semantic_discovery_profile(
    app_paths: AppPaths | None = None,
    config_path: str | Path | None = None,
    profile: str | None = None,
    home: str | Path | None = None,
) -> ResolvedModelProfile | None:
    try:
        paths = app_paths if app_paths is not None else AppPaths.discover(app_home=home)
        repository = ConfigRepository(paths, config_path=config_path)
        overrides = {"default_model_profile": profile} if profile is not None else None
        return repository.load(overrides=overrides).model_profile
    except Exception:
        return None


__all__ = ["resolve_semantic_discovery_profile"]
