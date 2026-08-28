"""Raw configuration compatibility helpers for the canonical profile owner."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from types import MappingProxyType
from typing import Any

from agent.llm.contracts import ProviderCapabilities, StructuredOutputMode

PROFILE_OVERRIDE_KEYS = ("api_url", "model", "temperature", "max_tokens", "timeout")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", "disabled"})


def freeze_provider_options(value: Any, active: set[int] | None = None) -> Any:
    """Freeze JSON-like provider options without retaining mutable aliases."""

    active_ids = active if active is not None else set()
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in active_ids:
            raise TypeError("provider_options must not contain cycles")
        active_ids.add(marker)
        try:
            return MappingProxyType(
                {key: freeze_provider_options(item, active_ids) for key, item in value.items()}
            )
        finally:
            active_ids.remove(marker)
    if isinstance(value, (list, tuple)):
        marker = id(value)
        if marker in active_ids:
            raise TypeError("provider_options must not contain cycles")
        active_ids.add(marker)
        try:
            return tuple(freeze_provider_options(item, active_ids) for item in value)
        finally:
            active_ids.remove(marker)
    if isinstance(value, (set, frozenset)):
        marker = id(value)
        if marker in active_ids:
            raise TypeError("provider_options must not contain cycles")
        active_ids.add(marker)
        try:
            return frozenset(freeze_provider_options(item, active_ids) for item in value)
        finally:
            active_ids.remove(marker)
    return deepcopy(value)


def thaw_provider_options(value: Any) -> Any:
    """Project frozen options back to fresh mutable compatibility data."""

    if isinstance(value, Mapping):
        return {key: thaw_provider_options(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_provider_options(item) for item in value]
    if isinstance(value, frozenset):
        return {thaw_provider_options(item) for item in value}
    return deepcopy(value)


def text_value(value: Any, default: str) -> str:
    normalized = default if value is None else str(value).strip()
    return normalized or default


def number_value(value: Any, default: float, *, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return default if minimum is not None and parsed < minimum else parsed


def integer_value(value: Any, default: int, *, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return default if minimum is not None and parsed < minimum else parsed


def boolean_value(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
    return default


def structured_mode(value: Any) -> StructuredOutputMode | None:
    if isinstance(value, StructuredOutputMode):
        return value
    return {
        "json": StructuredOutputMode.JSON_PROMPT,
        "json_prompt": StructuredOutputMode.JSON_PROMPT,
        "json-schema": StructuredOutputMode.JSON_SCHEMA,
        "json_schema": StructuredOutputMode.JSON_SCHEMA,
        "gbnf": StructuredOutputMode.GBNF,
        "none": StructuredOutputMode.NONE,
        "auto": StructuredOutputMode.AUTO,
    }.get(str(value or "").strip().casefold())


def capabilities_from_raw(raw: Any, *, legacy_flat: bool) -> ProviderCapabilities:
    if isinstance(raw, ProviderCapabilities):
        return raw
    values = raw if isinstance(raw, Mapping) else {}
    structured = values.get("structured_output", "gbnf" if legacy_flat else "json_prompt")
    if structured is True:
        structured = "gbnf" if legacy_flat else "json_prompt"
    modes: list[StructuredOutputMode] = []
    candidates = values.get("structured_output_modes")
    if isinstance(candidates, (list, tuple)):
        modes.extend(
            mode
            for candidate in candidates
            for mode in (structured_mode(candidate),)
            if mode is not None and mode not in modes
        )
    if not modes:
        mode = structured_mode(structured)
        if mode is not None and mode is not StructuredOutputMode.NONE:
            modes.append(mode)
    if modes and StructuredOutputMode.JSON_PROMPT not in modes:
        modes.append(StructuredOutputMode.JSON_PROMPT)
    return ProviderCapabilities(
        streaming=boolean_value(values.get("streaming", True), True),
        structured_output_modes=tuple(modes),
        reasoning=boolean_value(values.get("reasoning", legacy_flat), legacy_flat),
        token_counting=boolean_value(
            values.get("token_counting", legacy_flat), legacy_flat
        ),
        tool_calls=boolean_value(values.get("tool_calls", False), False),
    )


def provider_options_from_raw(raw: Any, *, legacy_flat: bool) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return deepcopy(dict(raw))
    return (
        {"reasoning_mode": "chat_template_kwargs", "tokenize_path": "/tokenize"}
        if legacy_flat
        else {}
    )


def gateway_profile_values(gateway: Any) -> dict[str, Any]:
    """Collect only the legacy profile declarations accepted at this edge."""

    values: dict[str, Any] = {}
    declared = getattr(gateway, "profile", None)
    if isinstance(declared, Mapping):
        values.update(deepcopy(dict(declared)))
    for field_name, attribute_name in (
        ("provider", "provider_name"),
        ("model", "model"),
        ("api_url", "api_url"),
        ("base_url", "base_url"),
        ("temperature", "temperature"),
        ("max_tokens", "max_tokens"),
        ("timeout", "timeout"),
        ("provider_options", "provider_options"),
    ):
        if field_name in values:
            continue
        value = getattr(gateway, attribute_name, None)
        supported = (
            isinstance(value, str)
            if field_name in {"provider", "model", "api_url", "base_url"}
            else isinstance(value, (str, int, float)) and not isinstance(value, bool)
            if field_name in {"temperature", "max_tokens", "timeout"}
            else isinstance(value, Mapping)
        )
        if supported and value not in (None, ""):
            values[field_name] = deepcopy(value)
    if "max_tokens" not in values:
        value = getattr(gateway, "max_output_tokens", None)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool) and value not in (None, ""):
            values["max_tokens"] = deepcopy(value)
    if "capabilities" not in values:
        capabilities = getattr(gateway, "capabilities", None)
        if isinstance(capabilities, ProviderCapabilities):
            values["capabilities"] = capabilities.to_dict()
        elif isinstance(capabilities, Mapping):
            values["capabilities"] = deepcopy(dict(capabilities))
    return values


def _apply_overrides(
    values: Mapping[str, Any], overrides: Mapping[str, Any] | None
) -> dict[str, Any]:
    copied = deepcopy(dict(values))
    if overrides is not None:
        for key in PROFILE_OVERRIDE_KEYS:
            if key in overrides:
                copied[key] = deepcopy(overrides[key])
    return copied


def _selected_name(config: Mapping[str, Any], explicit: str | None) -> str | None:
    if explicit is not None:
        return explicit
    candidate = config.get("default_model_profile")
    return candidate if isinstance(candidate, str) else None


def effective_profile_values(
    config: Mapping[str, Any],
    *,
    profile_name: str | None,
    overrides: Mapping[str, Any] | None,
) -> tuple[str, Mapping[str, Any], bool]:
    profiles = config.get("model_profiles")
    selected_name = _selected_name(config, profile_name)
    selected = (
        profiles.get(selected_name)
        if isinstance(profiles, Mapping) and isinstance(selected_name, str)
        else None
    )
    if isinstance(selected, Mapping):
        return text_value(selected_name, "legacy"), _apply_overrides(selected, overrides), True
    direct_profile = any(
        key in config for key in ("provider", "base_url", "capabilities", "provider_options")
    )
    if direct_profile:
        name = text_value(config.get("name") or config.get("profile"), "legacy")
        return name, _apply_overrides(config, overrides), True
    legacy = {key: config[key] for key in PROFILE_OVERRIDE_KEYS if key in config}
    return "legacy", _apply_overrides(legacy, overrides), False


__all__ = [
    "PROFILE_OVERRIDE_KEYS",
    "boolean_value",
    "capabilities_from_raw",
    "effective_profile_values",
    "freeze_provider_options",
    "gateway_profile_values",
    "integer_value",
    "number_value",
    "provider_options_from_raw",
    "structured_mode",
    "thaw_provider_options",
    "text_value",
]
