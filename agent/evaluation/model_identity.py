"""Campaign model profiles built from runtime identity primitives."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.llm.identity import (
    GENERIC_MODEL_ALIASES,
    model_config_fingerprint,
    normalize_endpoint_identity,
    normalize_external_identity,
)
from agent.llm.model_profile import resolve_model_profile

DEFAULT_PROFILE = "local_8gb"
def planned_model_profile(repo_root: str | Path, profile_name: str = DEFAULT_PROFILE) -> dict[str, Any]:
    """Return the frozen profile without constructing or probing a gateway."""

    config_path = Path(repo_root).resolve() / "agent" / "resources" / "default_config.json"
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    resolved = resolve_model_profile(raw, profile_name=profile_name)
    return {
        "provider": resolved.provider,
        "profile": resolved.name,
        "configured_model_id": resolved.model,
        "model": resolved.model,
        "temperature": resolved.temperature,
        "max_tokens": resolved.max_output_tokens,
        "timeout": resolved.timeout,
        "capabilities": resolved.capabilities.to_dict(),
        "provider_options": dict(resolved.to_dict()["provider_options"]),
        "endpoint_identity": resolved.endpoint_identity,
        "actual_provider_model_id": None,
        "actual_identity_available": False,
        "endpoint_policy": "not accessed before explicit live-model authorization",
    }


def model_config_identity(
    repo_root: str | Path,
    *,
    profile_name: str = DEFAULT_PROFILE,
    evidence_level: str = "real_model",
    actual_provider_model_id: str | None = None,
    external_identity: str | None = None,
) -> dict[str, Any]:
    planned = planned_model_profile(repo_root, profile_name)
    planned["actual_provider_model_id"] = actual_provider_model_id
    planned["actual_identity_available"] = actual_provider_model_id is not None
    planned["evidence_level"] = evidence_level
    frozen_external_identity = normalize_external_identity(external_identity)
    if frozen_external_identity is not None:
        planned["external_identity"] = frozen_external_identity
        planned["external_identity_source"] = "external_identity"
    fingerprint = model_config_fingerprint(planned)
    return {**planned, "model_config_fingerprint": fingerprint, "fingerprint": fingerprint}


def fake_model_identity() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "provider": "scripted-evaluation",
        "profile": "scripted-evaluation",
        "configured_model_id": "scripted-evaluation",
        "model": "scripted-evaluation",
        "temperature": 0.0,
        "max_tokens": 512,
        "timeout": None,
        "capabilities": {
            "streaming": False,
            "structured_output": "json_prompt",
            "reasoning": False,
            "token_counting": False,
            "tool_calls": False,
        },
        "provider_options": {},
        "endpoint_identity": "in-process://scripted-evaluation",
        "actual_provider_model_id": "scripted-evaluation",
        "actual_identity_available": True,
        "evidence_level": "deterministic",
    }
    fingerprint = model_config_fingerprint(payload)
    return {**payload, "model_config_fingerprint": fingerprint, "fingerprint": fingerprint}


__all__ = [
    "DEFAULT_PROFILE", "fake_model_identity", "model_config_identity",
    "GENERIC_MODEL_ALIASES", "normalize_endpoint_identity", "normalize_external_identity",
    "planned_model_profile",
]
