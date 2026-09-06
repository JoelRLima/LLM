"""Bounded, explicit online model health probe for ``doctor --online``."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from agent.health.online_model_support import (
    HEALTH_GRAMMAR,
    HEALTH_PROMPT,
    HEALTH_SCHEMA,
    HEALTH_SENTINEL,
    MAX_PROBE_TIMEOUT_SECONDS,
    _base_report,
    _invalid_configuration_report,
    _response_has_sentinel,
    _response_metadata,
    _restore_probe_timeout,
    _set_probe_timeout,
    _structured_request,
    complete_health_probe,
)
from agent.llm.contracts import (
    ModelMessage,
    ModelRequest,
)
from agent.llm.errors import (
    ModelConnectionError,
    ModelGatewayError,
    ModelResponseError,
    ModelTimeoutError,
    UnsupportedModelCapability,
)
from agent.llm.model_profile import ResolvedModelProfile, resolve_model_profile
from agent.llm.providers.factory import create_model_gateway


def _complete_probe(
    gateway: Any,
    request: ModelRequest,
    report: dict[str, Any],
    structured_required: bool,
    original_timeout: Any,
    profile: ResolvedModelProfile,
) -> tuple[bool, Any]:
    started = time.monotonic()
    try:
        return True, complete_health_probe(gateway, request, profile)
    except UnsupportedModelCapability:
        report.update(
            reachable=True,
            completion_ok=True,
            state="degraded",
            failure_reason="STRUCTURED_OUTPUT_UNSUPPORTED",
        )
        report["structured_probe_ok"] = (
            "unsupported" if structured_required else "failed"
        )
    except (ModelTimeoutError, TimeoutError):
        report.update(state="unavailable", failure_reason="PROBE_TIMEOUT")
        report["structured_probe_ok"] = "failed" if structured_required else "not_probed"
    except (ModelConnectionError, ConnectionError, ModelGatewayError, ModelResponseError):
        report.update(state="unavailable", failure_reason="PROVIDER_UNAVAILABLE")
        report["structured_probe_ok"] = "failed" if structured_required else "not_probed"
    except Exception:
        report.update(state="unavailable", failure_reason="PROBE_FAILED")
        report["structured_probe_ok"] = "failed" if structured_required else "not_probed"
    finally:
        report["latency_ms"] = round(max(0.0, time.monotonic() - started) * 1000, 3)
        _restore_probe_timeout(gateway, original_timeout)
    return False, None


def _project_probe_response(
    report: dict[str, Any],
    response: Any,
    structured_required: bool,
) -> dict[str, Any]:
    report["reachable"] = True
    report["completion_ok"] = True
    observed = _response_metadata(response)
    report["observed_model_identity"] = observed
    report["observed_model_identity_available"] = observed is not None
    sentinel_ok = _response_has_sentinel(response)
    if structured_required:
        report["structured_probe_ok"] = "ok" if sentinel_ok else "failed"
        report["state"] = "ready" if sentinel_ok else "degraded"
        report["online_ready"] = sentinel_ok
        if not sentinel_ok:
            report["failure_reason"] = "STRUCTURED_OUTPUT_FAILED"
    else:
        report["state"] = "ready" if sentinel_ok else "degraded"
        report["online_ready"] = sentinel_ok
        if not sentinel_ok:
            report["failure_reason"] = "INVALID_HEALTH_RESPONSE"
    return report


def run_online_model_health_probe(
    config: Mapping[str, Any] | ResolvedModelProfile | Any,
    *,
    profile_name: str | None = None,
    gateway_factory: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    """Run one bounded completion probe against the configured profile.

    The configured ResolvedModelProfile is resolved once and retained as the
    identity authority. Only the provider transport timeout is capped for this
    probe; that temporary value is reported separately.
    """

    try:
        profile = (
            config
            if isinstance(config, ResolvedModelProfile)
            else resolve_model_profile(config, profile_name=profile_name)
        )
    except (TypeError, ValueError, KeyError):
        return _invalid_configuration_report("INVALID_CONFIGURATION")

    supported = profile.provider == "openai_compatible" and bool(
        profile.api_url and profile.model and profile.endpoint_identity
    )
    if not supported:
        return _base_report(
            profile,
            configured=False,
            state="unavailable",
            failure_reason="UNSUPPORTED_PROVIDER_OR_ENDPOINT",
        )

    report = _base_report(profile, configured=True)
    structured, declared_mode, structured_required = _structured_request(profile)
    report["structured_output_declared_mode"] = declared_mode
    report["structured_output_required"] = structured_required
    factory = gateway_factory or create_model_gateway
    try:
        gateway = factory(profile)
    except (TypeError, ValueError, KeyError):
        report.update(state="unavailable", failure_reason="PROVIDER_UNAVAILABLE")
        return report

    timeout = float(report["probe_timeout_seconds"])
    changed_timeout, original_timeout = _set_probe_timeout(gateway, timeout)
    if not changed_timeout:
        report["transport_timeout_override_seconds"] = None
    request = ModelRequest(
        messages=(ModelMessage(role="system", content=HEALTH_PROMPT),),
        model=profile.model,
        temperature=0.0,
        max_output_tokens=min(64, max(1, int(profile.max_output_tokens))),
        stream=False,
        structured_output=structured,
        provider_options={},
    )
    report["request_count"] = 1
    completed, response = _complete_probe(
        gateway,
        request,
        report,
        structured_required,
        original_timeout,
        profile,
    )
    return report if not completed else _project_probe_response(
        report,
        response,
        structured_required,
    )

online_model_health_check = run_online_model_health_probe


__all__ = [
    "HEALTH_GRAMMAR",
    "HEALTH_PROMPT",
    "HEALTH_SCHEMA",
    "HEALTH_SENTINEL",
    "MAX_PROBE_TIMEOUT_SECONDS",
    "online_model_health_check",
    "run_online_model_health_probe",
]
