"""Canonical provider and model/config identity for Block 7 campaigns."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

DEFAULT_PROFILE = "local_8gb"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: bytes | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def normalize_endpoint_identity(value: Any) -> str | None:
    """Return a non-secret normalized endpoint identity without doing I/O."""

    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if "://" not in raw:
        return raw.casefold()
    parsed = urlsplit(raw)
    hostname = (parsed.hostname or "").casefold()
    if not hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        port = None
    netloc = hostname
    if port is not None and not (
        (parsed.scheme.casefold() == "http" and port == 80)
        or (parsed.scheme.casefold() == "https" and port == 443)
    ):
        netloc = f"{netloc}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.casefold(), netloc, path, "", ""))


def planned_model_profile(repo_root: str | Path, profile_name: str = DEFAULT_PROFILE) -> dict[str, Any]:
    """Return the frozen profile without constructing or probing a gateway."""

    config_path = Path(repo_root).resolve() / "agent" / "resources" / "default_config.json"
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    profiles = raw.get("model_profiles") if isinstance(raw, dict) else {}
    profile = dict(profiles.get(profile_name, {})) if isinstance(profiles, dict) else {}
    capabilities = profile.get("capabilities")
    capabilities = dict(capabilities) if isinstance(capabilities, Mapping) else {}
    endpoint = profile.get("base_url") or profile.get("api_url") or (
        raw.get("api_url") if isinstance(raw, dict) else None
    )
    provider_options = profile.get("provider_options")
    provider_options = dict(provider_options) if isinstance(provider_options, Mapping) else {}
    return {
        "provider": str(profile.get("provider", "openai_compatible")),
        "profile": profile_name,
        "configured_model_id": str(profile.get("model", raw.get("model", "default"))),
        "model": str(profile.get("model", raw.get("model", "default"))),
        "temperature": profile.get("temperature", 0.2),
        "max_tokens": profile.get("max_tokens", 2048),
        "timeout": profile.get("timeout", 300),
        "capabilities": {
            "streaming": bool(capabilities.get("streaming", True)),
            "structured_output": str(capabilities.get("structured_output", "gbnf")),
            "reasoning": bool(capabilities.get("reasoning", True)),
            "token_counting": bool(capabilities.get("token_counting", True)),
            "tool_calls": bool(capabilities.get("tool_calls", False)),
        },
        "provider_options": {
            key: value for key, value in sorted(provider_options.items())
            if str(key).casefold() not in {"authorization", "api_key", "password", "token", "secret"}
        },
        "endpoint_identity": normalize_endpoint_identity(endpoint),
        "actual_provider_model_id": None,
        "actual_identity_available": False,
        "endpoint_policy": "not accessed before explicit Phase 5 authorization",
    }


def model_config_identity(
    repo_root: str | Path,
    *,
    profile_name: str = DEFAULT_PROFILE,
    evidence_level: str = "real_model",
    actual_provider_model_id: str | None = None,
) -> dict[str, Any]:
    planned = planned_model_profile(repo_root, profile_name)
    planned["actual_provider_model_id"] = actual_provider_model_id
    planned["actual_identity_available"] = actual_provider_model_id is not None
    planned["evidence_level"] = evidence_level
    fingerprint = _sha256(_canonical_json(planned))
    return {**planned, "model_config_fingerprint": fingerprint, "fingerprint": fingerprint}


def fake_model_identity() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "provider": "block7-scripted",
        "profile": "block7-scripted",
        "configured_model_id": "block7-scripted",
        "model": "block7-scripted",
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
        "endpoint_identity": "in-process://block7-scripted",
        "actual_provider_model_id": "block7-scripted",
        "actual_identity_available": True,
        "evidence_level": "deterministic",
    }
    fingerprint = _sha256(_canonical_json(payload))
    return {**payload, "model_config_fingerprint": fingerprint, "fingerprint": fingerprint}


__all__ = [
    "DEFAULT_PROFILE", "fake_model_identity", "model_config_identity",
    "normalize_endpoint_identity", "planned_model_profile",
]
