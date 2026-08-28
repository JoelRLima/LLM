"""Canonical model-call metric projection."""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Dict

from agent.llm.contracts import normalize_usage, request_contract_value, response_usage


def _operation_field(operation: str | None) -> dict[str, str]:
    return {"operation": operation} if operation else {}


def build_model_call_metric(
    gateway: Any,
    config: Mapping[str, Any],
    started_at: float,
    *,
    success: bool,
    streaming: bool,
    response: Any = None,
    request: Any = None,
    call_number: int | None = None,
    estimated_tokens: int = 0,
    reserved_tokens: int = 0,
    estimated_request_tokens: int = 0,
    request_estimation_source: str = "unavailable",
    context_limit: int | None = None,
    context_compacted: bool = False,
    request_input_measurement: Any = None,
    operation: str | None = None,
) -> Dict[str, Any]:
    usage = response_usage(response)
    available = usage is not None and (
        usage.get("available", True) is not False
        if isinstance(usage, Mapping)
        else getattr(usage, "available", True) is not False
    )
    input_tokens, output_tokens, total_tokens, normalized_total, complete = normalize_usage(
        usage if available else None
    )
    if request_input_measurement is not None:
        measured_input_tokens = getattr(request_input_measurement, "token_count", None)
        measured_source = str(
            getattr(request_input_measurement, "source", request_estimation_source)
        )
        measured_exact = bool(getattr(request_input_measurement, "exact", False))
        measured_available = bool(
            getattr(request_input_measurement, "available", False)
        ) and isinstance(measured_input_tokens, int) and not isinstance(
            measured_input_tokens, bool
        )
    else:
        measured_input_tokens = (
            max(0, estimated_request_tokens)
            if request_estimation_source != "unavailable"
            or estimated_request_tokens > 0
            else None
        )
        measured_source = request_estimation_source
        measured_exact = measured_source == "provider_chat_input_tokens"
        measured_available = measured_input_tokens is not None
    if not measured_available:
        measured_input_tokens = None
    entry: Dict[str, Any] = {
        "type": "model_call",
        "metric_type": "model_call",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_ms": max(0, int((time.monotonic() - started_at) * 1000)),
        "success": bool(success),
        "provider_call_succeeded": bool(success),
        "streaming": bool(streaming),
        "provider": getattr(gateway, "provider_name", None),
        "model": getattr(gateway, "model", config.get("model")),
        "token_usage_complete": complete,
        "reserved_tokens": max(0, reserved_tokens),
        "request_input_tokens": measured_input_tokens,
        "request_input_measurement_source": measured_source,
        "request_input_measurement_exact": measured_exact,
        "request_input_measurement_available": measured_available,
        "estimated_request_tokens": measured_input_tokens or 0,
        "request_estimation_source": measured_source,
        "context_compacted": bool(context_compacted),
        "request_contract": request_contract_value(
            getattr(request, "request_contract", None)
        ),
        **_operation_field(operation),
    }
    if isinstance(context_limit, int) and not isinstance(context_limit, bool) and context_limit > 0:
        entry["context_limit"] = context_limit
        entry["request_utilization_ratio"] = (
            measured_input_tokens / context_limit
            if measured_input_tokens is not None
            else None
        )
    if call_number is not None:
        entry["call_number"] = call_number
    if input_tokens is not None:
        entry["input_tokens"] = entry["prompt_tokens"] = input_tokens
        entry["reported_input_tokens"] = input_tokens
    if output_tokens is not None:
        entry["output_tokens"] = entry["completion_tokens"] = output_tokens
        entry["reported_output_tokens"] = output_tokens
    if total_tokens is not None:
        entry["total_tokens"] = total_tokens
        entry["reported_total_tokens"] = total_tokens
    if input_tokens is not None and measured_input_tokens is not None:
        delta = input_tokens - measured_input_tokens
        entry["request_input_token_delta"] = delta
        entry["request_input_token_abs_delta"] = abs(delta)
        entry["request_input_token_consistent"] = delta == 0
    if complete:
        entry["estimated_tokens"] = 0
        assert normalized_total is not None
        entry["accounted_tokens"] = normalized_total
    else:
        entry["estimated_tokens"] = max(0, estimated_tokens)
        entry["accounted_tokens"] = max(0, estimated_tokens)
    return entry


__all__ = ["build_model_call_metric"]
