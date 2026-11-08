"""Safe setup of OpenAI-compatible profiles from local interfaces."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.application.configuration_admin import initialize_configuration
from llm_agent.application.context import AppPaths

_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _validate_profile(name: str, model: str, base_url: str) -> tuple[str, str, str]:
    if not isinstance(name, str) or _PROFILE_NAME.fullmatch(name.strip()) is None:
        raise ValueError("Use um nome de perfil com até 64 letras, números, ponto, '_' ou '-'.")
    selected_name = name.strip()
    if (
        not isinstance(model, str)
        or not model.strip()
        or len(model.strip()) > 256
        or any(ord(char) < 32 for char in model)
    ):
        raise ValueError("Informe o identificador do modelo (até 256 caracteres).")
    if not isinstance(base_url, str) or len(base_url.strip()) > 2048:
        raise ValueError("Informe uma URL base HTTP ou HTTPS válida.")
    endpoint = base_url.strip().rstrip("/")
    parsed = None
    try:
        parsed = urlsplit(endpoint)
        valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname) and parsed.port != 0
    except ValueError:
        valid = False
    if (not valid or parsed is None or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or any(ord(char) < 32 for char in endpoint)):
        raise ValueError("Use uma URL base HTTP/HTTPS sem credenciais, query ou fragmento.")
    return selected_name, model.strip(), endpoint


def configure_compatible_profile(
    app_paths: AppPaths,
    name: str,
    model: str,
    base_url: str,
    *,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create/update a profile without accepting or persisting an API secret."""
    selected_name, selected_model, endpoint = _validate_profile(name, model, base_url)
    repository = ConfigRepository(app_paths, config_path=config_path)
    if not repository.path.exists():
        initialize_configuration(app_paths, config_path)
    configured = repository.update({
        "default_model_profile": selected_name,
        "model_profiles": {
            selected_name: {
                "provider": "openai_compatible",
                "model": selected_model,
                "base_url": endpoint,
                # ConfigRepository merges profile dictionaries recursively. Set
                # the derived endpoint too, so an older explicit api_url on a
                # profile being updated cannot silently override this URL.
                "api_url": f"{endpoint}/chat/completions",
            }
        },
    }).to_dict()
    return {
        "config_path": str(repository.path),
        "default_model_profile": configured["default_model_profile"],
        "profile": {
            "name": selected_name,
            "provider": "openai_compatible",
            "model": selected_model,
        },
    }


__all__ = ["configure_compatible_profile"]
