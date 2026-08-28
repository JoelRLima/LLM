"""Runtime-owned provider and model identity projections."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from agent.llm.identity_safety import canonicalize_identity_value, redact_identity
from agent.llm.model_profile_binding import cached_gateway_model_profile

GENERIC_MODEL_ALIASES = frozenset({"default"})
_IDENTITY_FIELDS = (
    "call_index",
    "provider",
    "endpoint_identity",
    "declared_model",
    "observed_provider_model_id",
    "identity_source",
)


def canonical_json(value: Any) -> str:
    """Encode identity/fingerprint data deterministically."""

    return json.dumps(
        canonicalize_identity_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def sha256_digest(value: bytes | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def model_config_fingerprint(config: Mapping[str, Any]) -> str:
    """Fingerprint a non-secret declared model/provider configuration."""

    return sha256_digest(canonical_json(redact_identity(config)))


def normalize_endpoint_identity(value: Any) -> str | None:
    """Return a bounded, non-secret endpoint identity without doing I/O."""

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


def normalize_external_identity(value: Any) -> str | None:
    """Bound an explicit non-provider identity without probing an endpoint."""

    if value in (None, ""):
        return None
    identity = str(value).strip()[:256]
    if not identity or identity.casefold() in GENERIC_MODEL_ALIASES:
        return None
    return identity


def bounded_identity_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value).strip()[:256]


def call_identity(gateway: Any, request: Any, call_index: int) -> dict[str, Any]:
    return {
        "call_index": call_index,
        "provider": bounded_identity_text(getattr(gateway, "provider_name", None)),
        "endpoint_identity": bounded_identity_text(
            normalize_endpoint_identity(getattr(gateway, "endpoint_identity", None))
        ),
        "declared_model": bounded_identity_text(
            getattr(request, "model", None) or getattr(gateway, "model", None)
        ),
        "observed_provider_model_id": None,
        "identity_source": "unavailable",
    }


def observed_provider_model_id(metadata: Any) -> str | None:
    if not isinstance(metadata, Mapping):
        return None
    for key in ("observed_provider_model_id", "provider_model_id", "model_id", "model"):
        value = metadata.get(key)
        if value not in (None, ""):
            return bounded_identity_text(value)
    return None


def declared_provider_identity(gateway: Any, profile: Any = None) -> dict[str, Any]:
    """Project secret-safe identity from the relevant resolved profile."""

    raw_profile = getattr(gateway, "profile", None)
    canonical_profile = profile if callable(getattr(profile, "to_dict", None)) else getattr(gateway, "resolved_profile", None)
    if not callable(getattr(canonical_profile, "to_dict", None)):
        canonical_profile = cached_gateway_model_profile(gateway)
    if canonical_profile is not None and callable(getattr(canonical_profile, "to_dict", None)):
        profile = canonical_profile.to_dict()
        declared_provider = getattr(canonical_profile, "provider", None)
        declared_model = getattr(canonical_profile, "model", None)
        declared_capabilities = getattr(canonical_profile, "capabilities", None)
    else:
        profile = redact_identity(raw_profile) if isinstance(raw_profile, Mapping) else {}
        declared_provider = getattr(gateway, "provider_name", None)
        declared_model = getattr(gateway, "model", None)
        declared_capabilities = getattr(gateway, "capabilities", None)
    capabilities = declared_capabilities
    identity = {
        "provider": str(declared_provider or ""),
        "model": str(declared_model or ""),
        "profile": profile,
        "capabilities": {
            "streaming": bool(getattr(capabilities, "streaming", False)),
            "structured_output_modes": [
                str(getattr(mode, "value", mode))
                for mode in getattr(capabilities, "structured_output_modes", ())
            ],
            "reasoning": bool(getattr(capabilities, "reasoning", False)),
            "token_counting": bool(getattr(capabilities, "token_counting", False)),
            "tool_calls": bool(getattr(capabilities, "tool_calls", False)),
        },
        "endpoint_identity": normalize_endpoint_identity(
            profile.get("endpoint_identity")
            or profile.get("base_url")
            or profile.get("api_url")
            or getattr(gateway, "endpoint_identity", None)
        ),
        "actual_provider_model_id": getattr(gateway, "provider_model_id", None),
    }
    canonical_fingerprint = getattr(canonical_profile, "fingerprint", None)
    identity["model_config_fingerprint"] = (
        canonical_fingerprint
        if isinstance(canonical_fingerprint, str) and canonical_fingerprint
        else model_config_fingerprint(identity)
    )
    return identity


def unavailable_observed_identity(
    endpoint_identity: Any = None,
) -> dict[str, Any]:
    """Represent unavailable or insufficient provider observation explicitly."""

    endpoint = normalize_endpoint_identity(endpoint_identity)
    return {
        "available": False,
        "provider_observation_available": False,
        "identity_sufficient": False,
        "consistent": True,
        "specific": False,
        "complete": False,
        "provider_model_id": None,
        "actual_provider_model_id": None,
        "provider": None,
        "model": None,
        "endpoint_identity": endpoint,
        "source": "unavailable",
        "identity_source": "unavailable",
        "observed_model_ids": [],
        "distinct_observed_model_ids": [],
        "external_identity": None,
        "external_identity_source": None,
        "provider_observation_limitation": "backend_identity_unavailable",
        "call_count": 0,
        "call_identities": [],
    }


def project_observed_provider_identity(
    records: Sequence[Mapping[str, Any]],
    declared: Mapping[str, Any],
    *,
    external_identity: Any = None,
) -> dict[str, Any]:
    """Reconcile per-call observations into one bounded provider identity."""

    call_identities = [
        {key: record.get(key) for key in _IDENTITY_FIELDS}
        for record in records
        if isinstance(record, Mapping)
    ]
    observed_ids = [
        str(identity["observed_provider_model_id"])
        for identity in call_identities
        if identity.get("observed_provider_model_id") not in (None, "")
    ]
    distinct_observed_ids = list(dict.fromkeys(observed_ids))
    generic_aliases = GENERIC_MODEL_ALIASES
    specific = len(distinct_observed_ids) == 1 and distinct_observed_ids[0].casefold() not in generic_aliases
    normalized_external = normalize_external_identity(external_identity)
    providers = list(dict.fromkeys(
        str(identity["provider"]).strip()
        for identity in call_identities
        if identity.get("provider") not in (None, "")
    ))
    endpoints = list(dict.fromkeys(
        str(identity["endpoint_identity"]).strip()
        for identity in call_identities
        if identity.get("endpoint_identity") not in (None, "")
    ))
    consistent = len(distinct_observed_ids) <= 1 and len(providers) <= 1 and len(endpoints) <= 1
    provider_observed = bool(distinct_observed_ids)
    fields_complete = bool(call_identities) and all(
        all(key in identity for key in _IDENTITY_FIELDS)
        for identity in call_identities
    )
    provider_observation_complete = bool(call_identities) and all(
        identity.get("observed_provider_model_id") not in (None, "")
        for identity in call_identities
    )
    complete = bool(
        fields_complete
        and (provider_observation_complete or normalized_external is not None)
    )
    observed_model_id = distinct_observed_ids[0] if len(distinct_observed_ids) == 1 else None
    provider_identity = providers[0] if len(providers) == 1 else None
    endpoint_identity = endpoints[0] if len(endpoints) == 1 else None
    observed_source = (
        "response.provider_metadata"
        if provider_observed and specific
        else "external_identity"
        if normalized_external
        else "response.provider_metadata"
        if provider_observed
        else "unavailable"
    )
    return {
        "available": provider_observed or bool(normalized_external),
        "provider_observation_available": provider_observed,
        "identity_sufficient": bool(complete and consistent and (specific or normalized_external)),
        "consistent": consistent,
        "specific": specific,
        "complete": complete,
        "provider_observation_complete": provider_observation_complete,
        "provider_model_id": observed_model_id,
        "actual_provider_model_id": observed_model_id,
        "model": observed_model_id if provider_observed else None,
        "provider": provider_identity,
        "endpoint_identity": endpoint_identity or normalize_endpoint_identity(declared.get("endpoint_identity")),
        "source": observed_source,
        "identity_source": observed_source,
        "observed_model_ids": observed_ids,
        "distinct_observed_model_ids": distinct_observed_ids,
        "external_identity": normalized_external,
        "external_identity_source": "external_identity" if normalized_external else None,
        "provider_observation_limitation": (
            "generic_provider_model_id"
            if provider_observed and not specific
            else "backend_identity_unavailable"
            if not provider_observed
            else None
        ),
        "call_count": len(call_identities),
        "call_identities": call_identities,
    }


__all__ = [
    "GENERIC_MODEL_ALIASES",
    "bounded_identity_text",
    "call_identity",
    "canonical_json",
    "declared_provider_identity",
    "model_config_fingerprint",
    "normalize_endpoint_identity",
    "normalize_external_identity",
    "observed_provider_model_id",
    "project_observed_provider_identity",
    "redact_identity",
    "sha256_digest",
    "unavailable_observed_identity",
]
