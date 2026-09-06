"""Shared constants and helpers for the bounded online health probe."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agent.cancellation import CancellationToken
from agent.llm.contracts import StructuredOutputMode, StructuredOutputRequest
from agent.llm.identity import observed_provider_model_id
from agent.llm.model_profile import ResolvedModelProfile
from agent.runtime.context import RuntimeLimits, TaskExecutionContext
from agent.runtime.model_call import ModelCallService

HEALTH_SENTINEL = "W13_HEALTH_OK"
HEALTH_PROMPT = (
    'Return exactly the JSON object {"sentinel":"W13_HEALTH_OK"} and nothing else.'
)
HEALTH_GRAMMAR = r'''root ::= "{\"sentinel\":\"W13_HEALTH_OK\"}"'''
HEALTH_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "sentinel": {
            "type": "string",
            "enum": [HEALTH_SENTINEL],
        }
    },
    "required": ["sentinel"],
    "additionalProperties": False,
}
MAX_PROBE_TIMEOUT_SECONDS = 30.0
_MISSING_TIMEOUT = object()


def complete_health_probe(
    gateway: Any,
    request: Any,
    profile: ResolvedModelProfile,
) -> Any:
    """Route the bounded health completion through the canonical call owner."""

    context = TaskExecutionContext(
        model_gateway=gateway,
        cancellation=CancellationToken(),
        model_profile=profile,
        limits=RuntimeLimits(
            max_model_calls=1,
            max_output_tokens=request.max_output_tokens,
        ),
    )
    return ModelCallService.for_context(context).complete(
        request,
        operation="online_model_health_probe",
    ).response


def _profile_text(value: Any, *, limit: int = 256) -> str:
    return str(value or "").strip()[:limit]


def _base_report(
    profile: ResolvedModelProfile | None,
    *,
    configured: bool,
    state: str = "not_run",
    failure_reason: str | None = None,
) -> dict[str, Any]:
    capabilities = profile.capabilities if profile is not None else None
    modes = tuple(getattr(capabilities, "structured_output_modes", ()))
    declared_mode = (
        str(getattr(modes[0], "value", modes[0]))
        if modes
        else "none"
    )
    token_counting = bool(getattr(capabilities, "token_counting", False))
    streaming = bool(getattr(capabilities, "streaming", False))
    configured_timeout = (
        float(profile.timeout)
        if profile is not None
        else None
    )
    probe_timeout = (
        min(configured_timeout, MAX_PROBE_TIMEOUT_SECONDS)
        if configured_timeout is not None
        else MAX_PROBE_TIMEOUT_SECONDS
    )
    return {
        "configured": configured,
        "reachable": False,
        "completion_ok": False,
        "online_ready": False,
        "state": state,
        "configured_profile_name": (
            _profile_text(profile.name, limit=128) if profile is not None else None
        ),
        "runtime_profile_fingerprint": (
            _profile_text(profile.fingerprint, limit=128) if profile is not None else None
        ),
        "configured_endpoint_identity": (
            _profile_text(profile.endpoint_identity, limit=256)
            if profile is not None
            else None
        ),
        "declared_provider": (
            _profile_text(profile.provider, limit=128) if profile is not None else None
        ),
        "declared_model": (
            _profile_text(profile.model, limit=256) if profile is not None else None
        ),
        "observed_model_identity": None,
        "observed_model_identity_available": False,
        "structured_output_declared_mode": declared_mode,
        "structured_output_required": bool(modes),
        "structured_probe_ok": "not_probed",
        "token_counting_declared": token_counting,
        "token_count_probe_ok": "not_probed",
        "streaming_declared": streaming,
        "streaming_probe": "not_probed",
        "configured_timeout_seconds": configured_timeout,
        "probe_timeout_seconds": probe_timeout,
        "transport_timeout_override_seconds": probe_timeout,
        "latency_ms": None,
        "request_count": 0,
        "failure_reason": failure_reason,
    }


def _invalid_configuration_report(reason: str) -> dict[str, Any]:
    report = _base_report(None, configured=False, state="unavailable", failure_reason=reason)
    report["structured_output_required"] = False
    return report


def _structured_request(
    profile: ResolvedModelProfile,
) -> tuple[StructuredOutputRequest | None, str, bool]:
    modes = tuple(profile.capabilities.structured_output_modes)
    if not modes:
        return None, "none", False
    declared = modes[0]
    if not isinstance(declared, StructuredOutputMode):
        try:
            declared = StructuredOutputMode(str(declared))
        except ValueError:
            return None, "unsupported", True
    if declared is StructuredOutputMode.NONE:
        return None, declared.value, False
    effective = (
        StructuredOutputMode.JSON_PROMPT
        if declared is StructuredOutputMode.AUTO
        else declared
    )
    if effective is StructuredOutputMode.GBNF:
        return (
            StructuredOutputRequest(
                mode=effective,
                grammar=HEALTH_GRAMMAR,
                instruction=HEALTH_PROMPT,
            ),
            declared.value,
            True,
        )
    if effective is StructuredOutputMode.JSON_SCHEMA:
        return (
            StructuredOutputRequest(
                mode=effective,
                schema=dict(HEALTH_SCHEMA),
                instruction=HEALTH_PROMPT,
            ),
            declared.value,
            True,
        )
    if effective is StructuredOutputMode.JSON_PROMPT:
        return (
            StructuredOutputRequest(
                mode=effective,
                schema=dict(HEALTH_SCHEMA),
                instruction=HEALTH_PROMPT,
            ),
            declared.value,
            True,
        )
    return None, "unsupported", True


def _response_has_sentinel(response: Any) -> bool:
    content = getattr(response, "content", None)
    if not isinstance(content, str):
        if isinstance(response, Mapping):
            content = response.get("content")
        else:
            content = None
    if not isinstance(content, str):
        return False
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return False
    return (
        isinstance(value, dict)
        and set(value) == {"sentinel"}
        and value.get("sentinel") == HEALTH_SENTINEL
    )


def _response_metadata(response: Any) -> str | None:
    metadata = getattr(response, "provider_metadata", None)
    if metadata is None and isinstance(response, Mapping):
        metadata = response.get("provider_metadata")
    return observed_provider_model_id(metadata)


def _set_probe_timeout(gateway: Any, timeout: float) -> tuple[bool, Any]:
    original = getattr(gateway, "timeout", _MISSING_TIMEOUT)
    if original is not _MISSING_TIMEOUT:
        try:
            gateway.timeout = timeout
        except (AttributeError, TypeError):
            return False, original
    return True, original


def _restore_probe_timeout(gateway: Any, original: Any) -> None:
    if original is not _MISSING_TIMEOUT:
        try:
            gateway.timeout = original
        except (AttributeError, TypeError):
            pass
