"""UI-neutral configuration projections for first-run and chat entry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from llm_agent.agent.runtime.config_errors import ConfigError, ConfigNotFound
from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.application.configuration_errors import ConfigurationError, translate_configuration_errors
from llm_agent.application.context import AppPaths


@dataclass(frozen=True)
class FirstRunProfileView:
    name: str
    model: str
    endpoint: str


@dataclass(frozen=True)
class FirstRunConfigurationView:
    path: Path
    default_profile: str
    profiles: tuple[FirstRunProfileView, ...]


def read_first_run_configuration(
    app_paths: AppPaths,
    config_path: str | Path | None = None,
) -> FirstRunConfigurationView:
    with translate_configuration_errors():
        repository = ConfigRepository(app_paths, config_path=config_path)
        document = repository.load(environment={}).to_dict()
    raw_profiles = document.get("model_profiles", {})
    if not isinstance(raw_profiles, dict):
        raise ConfigurationError("Invalid configuration: model_profiles must be an object.")
    projected: list[FirstRunProfileView] = []
    for name, raw_profile in raw_profiles.items():
        if not isinstance(name, str) or not isinstance(raw_profile, dict):
            raise ConfigurationError("Invalid configuration: model profile must be an object.")
        projected.append(
            FirstRunProfileView(
                name=name,
                model=str(raw_profile.get("model") or document.get("model") or "default"),
                endpoint=str(
                    raw_profile.get("base_url")
                    or raw_profile.get("api_url")
                    or document.get("api_url")
                    or ""
                ),
            )
        )
    profiles = tuple(projected)
    profile_names = tuple(profile.name for profile in profiles)
    default_profile = str(
        document.get("default_model_profile", profile_names[0] if profile_names else "")
    )
    return FirstRunConfigurationView(repository.path, default_profile, profiles)


def update_first_run_configuration(
    app_paths: AppPaths,
    selected_profile: str,
    model: str,
    endpoint: str,
    config_path: str | Path | None = None,
) -> None:
    with translate_configuration_errors():
        repository = ConfigRepository(app_paths, config_path=config_path)
        repository.update(
            {
                "default_model_profile": selected_profile,
                "model_profiles": {
                    selected_profile: {"model": model, "base_url": endpoint}
                },
            }
        )
        repository.load(environment={})


def configuration_ready_for_chat_entry(
    app_paths: AppPaths,
    config_path: str | Path | None = None,
) -> bool:
    config_file = (
        Path(config_path).expanduser().resolve()
        if config_path is not None
        else app_paths.config_file
    )
    if not config_file.is_file():
        return False
    try:
        ConfigRepository(app_paths, config_path=config_path).load()
    except (ConfigError, ConfigNotFound, OSError, ValueError):
        return False
    return True


__all__ = [
    "FirstRunProfileView",
    "FirstRunConfigurationView",
    "read_first_run_configuration",
    "update_first_run_configuration",
    "configuration_ready_for_chat_entry",
]
