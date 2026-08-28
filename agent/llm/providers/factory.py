"""Factory de providers com compatibilidade para a configuração legada."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.llm.contracts import LegacyPayloadGateway
from agent.llm.model_profile import ResolvedModelProfile
from agent.llm.model_profile import resolve_model_profile as resolve_canonical_model_profile
from agent.llm.providers.openai_compatible import OpenAICompatibleGateway

SUPPORTED_MODEL_PROVIDERS = frozenset({"openai_compatible"})


def resolve_model_profile(config: Mapping[str, Any] | Any) -> ResolvedModelProfile:
    """Compatibility facade for the one canonical profile owner."""

    return resolve_canonical_model_profile(config)


def create_model_gateway(config: Mapping[str, Any] | Any) -> LegacyPayloadGateway:
    profile = resolve_model_profile(config)
    provider = str(profile.get("provider", "openai_compatible"))
    if provider == "openai_compatible":
        return OpenAICompatibleGateway(profile)
    raise ValueError(f"Provider de modelo não suportado: {provider}")


__all__ = [
    "SUPPORTED_MODEL_PROVIDERS",
    "create_model_gateway",
    "resolve_model_profile",
]
