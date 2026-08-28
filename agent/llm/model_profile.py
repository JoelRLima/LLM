"""Canonical, typed effective model/profile configuration.

Configuration files and old callers still arrive as mappings.  This module is
the single ingress that turns those compatibility shapes into the immutable
runtime profile consumed by providers and model-request builders.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from agent.llm.contracts import ProviderCapabilities
from agent.llm.identity import (
    model_config_fingerprint,
    normalize_endpoint_identity,
    redact_identity,
)
from agent.llm.model_profile_binding import (
    cached_gateway_model_profile,
    remember_gateway_model_profile,
)
from agent.llm.model_profile_compat import (
    PROFILE_OVERRIDE_KEYS,
    capabilities_from_raw,
    effective_profile_values,
    freeze_provider_options,
    gateway_profile_values,
    integer_value,
    number_value,
    provider_options_from_raw,
    text_value,
    thaw_provider_options,
)

DEFAULT_API_URL = "http://127.0.0.1:8080/v1/chat/completions"
DEFAULT_PROVIDER = "openai_compatible"
DEFAULT_MODEL = "default"
DEFAULT_TEMPERATURE = 0.6
DEFAULT_TIMEOUT = 300.0


def _config_mapping(config: Any) -> Mapping[str, Any]:
    to_runtime_dict = getattr(config, "to_runtime_dict", None)
    if callable(to_runtime_dict):
        value = to_runtime_dict()
        if isinstance(value, Mapping):
            return value
    to_dict = getattr(config, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
        if isinstance(value, Mapping):
            return value
    if isinstance(config, Mapping):
        return config
    raise TypeError("model configuration must be a mapping or resolved config")


@dataclass(frozen=True, slots=True)
class ResolvedModelProfile(Mapping[str, Any]):
    """One effective model profile used by all runtime consumers."""

    name: str
    provider: str
    model: str
    api_url: str
    base_url: str | None
    temperature: float
    max_output_tokens: int
    timeout: float
    capabilities: ProviderCapabilities
    provider_options: Mapping[str, Any] = field(default_factory=dict, repr=False)
    endpoint_identity: str | None = None
    fingerprint: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_options", freeze_provider_options(self.provider_options))
        if self.endpoint_identity is None:
            object.__setattr__(
                self,
                "endpoint_identity",
                normalize_endpoint_identity(self.api_url),
            )
        if not self.fingerprint:
            object.__setattr__(
                self,
                "fingerprint",
                model_config_fingerprint(self.identity_payload()),
            )

    @property
    def profile_name(self) -> str:
        return self.name

    @property
    def max_tokens(self) -> int:
        return self.max_output_tokens

    @property
    def model_config_fingerprint(self) -> str:
        return self.fingerprint

    def identity_payload(self) -> dict[str, Any]:
        return {
            "profile": self.name,
            "provider": self.provider,
            "model": self.model,
            "api_url": thaw_provider_options(
                redact_identity({"api_url": self.api_url})["api_url"]
            ),
            "endpoint_identity": self.endpoint_identity,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "timeout": self.timeout,
            "capabilities": self.capabilities.to_dict(),
            "provider_options": redact_identity(thaw_provider_options(self.provider_options)),
        }

    def to_dict(self, *, include_secrets: bool = False) -> dict[str, Any]:
        result = {
            "name": self.name,
            "profile": self.name,
            "provider": self.provider,
            "model": self.model,
            "api_url": self.api_url,
            "base_url": self.base_url,
            "endpoint_identity": self.endpoint_identity,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "max_output_tokens": self.max_output_tokens,
            "timeout": self.timeout,
            "capabilities": self.capabilities.to_dict(),
            "provider_options": thaw_provider_options(self.provider_options),
            "model_config_fingerprint": self.fingerprint,
            "fingerprint": self.fingerprint,
        }
        return result if include_secrets else redact_identity(result)

    def to_runtime_dict(self) -> dict[str, Any]:
        return self.to_dict(include_secrets=True)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


def resolve_model_profile(
    config: Any,
    *,
    profile_name: str | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> ResolvedModelProfile:
    """Resolve one profile; all raw/legacy interpretation ends here."""

    values = _config_mapping(config)
    selected_name, raw_profile, named_profile = effective_profile_values(
        values,
        profile_name=profile_name,
        overrides=overrides,
    )
    from agent.runtime.hardware import resolve_hardware_profile

    hardware = resolve_hardware_profile(dict(values))
    provider = text_value(raw_profile.get("provider"), DEFAULT_PROVIDER)
    model = text_value(raw_profile.get("model"), text_value(values.get("model"), DEFAULT_MODEL))
    temperature = number_value(
        raw_profile.get("temperature", values.get("temperature", DEFAULT_TEMPERATURE)),
        DEFAULT_TEMPERATURE,
        minimum=0.0,
    )
    output_default = hardware.default_output_tokens or 1024
    max_output_tokens = integer_value(
        raw_profile.get("max_tokens", values.get("max_tokens", output_default)),
        output_default,
        minimum=1,
    )
    timeout = number_value(
        raw_profile.get("timeout", values.get("timeout", DEFAULT_TIMEOUT)),
        DEFAULT_TIMEOUT,
        minimum=1.0,
    )
    explicit_api_url = raw_profile.get("api_url")
    explicit_base_url = raw_profile.get("base_url")
    base_url = (
        text_value(explicit_base_url, "").rstrip("/")
        if explicit_base_url not in (None, "")
        else None
    )
    if explicit_api_url not in (None, ""):
        api_url = text_value(explicit_api_url, DEFAULT_API_URL)
    elif base_url:
        api_url = f"{base_url}/chat/completions"
    else:
        api_url = text_value(values.get("api_url"), DEFAULT_API_URL)
    if base_url is None and api_url.endswith("/chat/completions"):
        derived_base = api_url[: -len("/chat/completions")].rstrip("/")
        base_url = derived_base or None

    raw_capabilities = raw_profile.get("capabilities")
    if raw_capabilities is None and not named_profile:
        raw_capabilities = {
            "structured_output": (
                "gbnf"
                if str(values.get("ENABLE_GBNF", True)).strip().casefold()
                in {"1", "true", "yes", "on", "enabled"}
                else "json_prompt"
            )
        }
    capabilities = capabilities_from_raw(
        raw_capabilities,
        legacy_flat=not named_profile,
    )
    options = provider_options_from_raw(
        raw_profile.get("provider_options"),
        legacy_flat=not named_profile,
    )
    endpoint_identity = normalize_endpoint_identity(api_url)
    return ResolvedModelProfile(
        name=selected_name,
        provider=provider,
        model=model,
        api_url=api_url,
        base_url=base_url,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        timeout=timeout,
        capabilities=capabilities,
        provider_options=options,
        endpoint_identity=endpoint_identity,
    )


def resolve_gateway_model_profile(
    config: Any,
    gateway: Any,
) -> ResolvedModelProfile:
    """Resolve one profile while preserving injected-gateway compatibility.

    A gateway-provided canonical profile wins.  Otherwise its supported legacy
    declarations are merged over the *current* effective configuration and
    passed once through ``resolve_model_profile``.  The weak gateway binding
    below is only a projection for callers that lack an explicit profile; it
    is never consulted before this resolution.
    """

    canonical = getattr(gateway, "resolved_profile", None)
    if isinstance(canonical, ResolvedModelProfile):
        return canonical
    declared = gateway_profile_values(gateway)
    if declared:
        values = deepcopy(dict(_config_mapping(config)))
        profiles = values.get("model_profiles")
        selected_name = values.get("default_model_profile")
        selected = (
            profiles.get(selected_name)
            if isinstance(profiles, Mapping) and isinstance(selected_name, str)
            else None
        )
        if isinstance(selected, Mapping):
            selected_values = deepcopy(dict(selected))
            values.update(selected_values)
            values.pop("model_profiles", None)
            values.pop("default_model_profile", None)
            values.setdefault("name", selected_name)
        values.update(declared)
        resolved = resolve_model_profile(values)
    else:
        resolved = resolve_model_profile(config)

    previous = cached_gateway_model_profile(gateway)
    if isinstance(previous, ResolvedModelProfile) and previous == resolved:
        return previous
    remember_gateway_model_profile(gateway, resolved)
    return resolved


__all__ = [
    "DEFAULT_API_URL",
    "PROFILE_OVERRIDE_KEYS",
    "ResolvedModelProfile",
    "resolve_gateway_model_profile",
    "resolve_model_profile",
]
