"""Declared model identity projection for bounded audit and metrics."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def project_declared_model_audit_identity(
    gateway: Any,
    profile: Any = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, str | None]:
    """Project declared, bounded model identity without provider execution."""

    from agent.llm.identity import bounded_identity_text, model_config_fingerprint, normalize_endpoint_identity

    selected_profile = profile
    if selected_profile is None:
        selected_profile = getattr(gateway, "resolved_profile", None)
    if selected_profile is None:
        from agent.llm.model_profile_binding import cached_gateway_model_profile

        selected_profile = cached_gateway_model_profile(gateway)
    profile_mapping = selected_profile if isinstance(selected_profile, Mapping) else {}
    provider = getattr(selected_profile, "provider", None) or profile_mapping.get("provider")
    model = getattr(selected_profile, "model", None) or profile_mapping.get("model")
    profile_name = (
        getattr(selected_profile, "profile_name", None)
        or getattr(selected_profile, "name", None)
        or profile_mapping.get("profile")
        or profile_mapping.get("name")
    )
    endpoint = (
        getattr(selected_profile, "endpoint_identity", None)
        or profile_mapping.get("endpoint_identity")
        or profile_mapping.get("api_url")
        or profile_mapping.get("base_url")
        or getattr(gateway, "endpoint_identity", None)
        or getattr(gateway, "api_url", None)
    )
    fingerprint = (
        getattr(selected_profile, "fingerprint", None)
        or getattr(selected_profile, "model_config_fingerprint", None)
        or profile_mapping.get("model_config_fingerprint")
        or profile_mapping.get("fingerprint")
    )
    if fingerprint is None and isinstance(config, Mapping):
        fingerprint = model_config_fingerprint(config)
    return {
        "provider": bounded_identity_text(provider or getattr(gateway, "provider_name", None)),
        "declared_model": bounded_identity_text(model or getattr(gateway, "model", None)),
        "profile_name": bounded_identity_text(profile_name),
        "endpoint_identity": bounded_identity_text(normalize_endpoint_identity(endpoint)),
        "model_config_fingerprint": bounded_identity_text(fingerprint),
    }


__all__ = ["project_declared_model_audit_identity"]
