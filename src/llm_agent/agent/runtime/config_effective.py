"""Materialize settings that depend on the selected model profile."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

PROFILE_OVERRIDE_KEYS = (
    "api_url",
    "model",
    "temperature",
    "max_tokens",
    "timeout",
)


def _profile_overrides(layers: tuple[Mapping[str, Any], ...]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for layer in layers:
        for key in PROFILE_OVERRIDE_KEYS:
            if key in layer:
                overrides[key] = layer[key]
    return overrides


def apply_selected_profile_overrides(
    config: dict[str, Any],
    *layers: Mapping[str, Any],
) -> None:
    """Apply only explicit environment/CLI values to the selected profile."""

    profile_name = config.get("default_model_profile")
    profiles = config.get("model_profiles")
    if not isinstance(profile_name, str) or not isinstance(profiles, dict):
        return
    selected = profiles.get(profile_name)
    if not isinstance(selected, dict):
        return

    effective = dict(selected)
    effective.update(_profile_overrides(layers))
    for layer in layers:
        if "ENABLE_GBNF" not in layer:
            continue
        capabilities = dict(effective.get("capabilities") or {})
        capabilities["structured_output"] = (
            "gbnf" if layer["ENABLE_GBNF"] else "json_prompt"
        )
        effective["capabilities"] = capabilities

    updated_profiles = dict(profiles)
    updated_profiles[profile_name] = effective
    config["model_profiles"] = updated_profiles


__all__ = ["PROFILE_OVERRIDE_KEYS", "apply_selected_profile_overrides"]
