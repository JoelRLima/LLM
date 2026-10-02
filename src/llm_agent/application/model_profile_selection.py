"""Application use case for selecting and persisting the default model profile."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from llm_agent.agent.llm.model_profile import (
    resolve_model_profile as _resolve_model_profile,
)
from llm_agent.agent.runtime.config_repository import (
    ConfigRepository as _ConfigRepository,
)
from llm_agent.application.context import AppPaths


def select_default_model_profile(
    effective_config: Mapping[str, Any],
    selected_profile: str,
    app_paths: AppPaths,
    config_path: str | Path | None = None,
) -> None:
    """Validate against the active configuration, then persist its selection."""

    _resolve_model_profile(effective_config, profile_name=selected_profile)
    _ConfigRepository(app_paths, config_path=config_path).update(
        {"default_model_profile": selected_profile}
    )


__all__ = ["select_default_model_profile"]
