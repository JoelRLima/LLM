"""Factory de providers com compatibilidade para a configuração legada."""

from __future__ import annotations

from typing import Any, Dict

from agent.llm.contracts import LegacyPayloadGateway
from agent.llm.providers.openai_compatible import OpenAICompatibleGateway


def resolve_model_profile(config: Dict[str, Any]) -> Dict[str, Any]:
    profiles = config.get("model_profiles")
    default_name = config.get("default_model_profile")
    if isinstance(profiles, dict) and isinstance(default_name, str):
        selected = profiles.get(default_name)
        if isinstance(selected, dict):
            profile = dict(selected)
            profile.setdefault("name", default_name)
            return profile

    api_url = str(config.get("api_url", "http://127.0.0.1:8080/v1/chat/completions"))
    return {
        "name": "legacy",
        "provider": "openai_compatible",
        "api_url": api_url,
        "model": str(config.get("model", "default")),
        "timeout": config.get("timeout", 300),
        "capabilities": {
            "streaming": True,
            "structured_output": "gbnf" if config.get("ENABLE_GBNF", True) else "json_prompt",
            "reasoning": True,
            "token_counting": "endpoint",
        },
        "provider_options": {
            "reasoning_mode": "chat_template_kwargs",
            "tokenize_path": "/tokenize",
        },
    }


def create_model_gateway(config: Dict[str, Any]) -> LegacyPayloadGateway:
    profile = resolve_model_profile(config)
    provider = str(profile.get("provider", "openai_compatible"))
    if provider == "openai_compatible":
        return OpenAICompatibleGateway(profile)
    raise ValueError(f"Provider de modelo não suportado: {provider}")
