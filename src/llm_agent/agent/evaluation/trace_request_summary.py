"""Bounded request summary projection for evaluation traces."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from llm_agent.agent.llm.decision_contract import request_contract_value
from llm_agent.agent.runtime.budget_estimation import measure_model_request_input_tokens


def summarize_request(owner: Any, request: Any) -> dict[str, Any]:
    structured = getattr(request, "structured_output", None)
    raw_structured_mode = getattr(structured, "mode", "") if structured is not None else None
    structured_mode = (
        str(getattr(raw_structured_mode, "value", raw_structured_mode))
        if structured is not None
        else None
    )
    structured_contract_present = bool(
        structured is not None
        and (
            getattr(structured, "schema", None) is not None
            or getattr(structured, "grammar", None) not in (None, "")
            or getattr(structured, "instruction", None) not in (None, "")
            or structured_mode not in (None, "", "StructuredOutputMode.NONE", "none")
        )
    )
    request_input_measurement = owner._pending_request_measurements.pop(
        id(request), None
    )
    if request_input_measurement is None:
        request_input_measurement = measure_model_request_input_tokens(
            request, owner.gateway
        )
    estimated_input_tokens = request_input_measurement.token_count or 0
    estimation_source = request_input_measurement.source
    summary = {
        "model": str(getattr(request, "model", "")),
        "temperature": getattr(request, "temperature", None),
        "max_output_tokens": getattr(request, "max_output_tokens", None),
        "stream": bool(getattr(request, "stream", False)),
        "reasoning_budget": getattr(request, "reasoning_budget", None),
        "request_contract": request_contract_value(
            getattr(request, "request_contract", None)
        ),
        "structured_mode": structured_mode,
        "structured_contract_present": structured_contract_present,
        "structured_contract": {
            "mode": structured_mode,
            "schema_present": getattr(structured, "schema", None) is not None,
            "grammar_present": getattr(structured, "grammar", None) not in (None, ""),
            "instruction_present": getattr(structured, "instruction", None) not in (None, ""),
        } if structured is not None else None,
        "message_count": len(getattr(request, "messages", ()) or ()),
        "request_input_tokens": request_input_measurement.token_count,
        "request_input_measurement_source": estimation_source,
        "request_input_measurement_exact": request_input_measurement.exact,
        "request_input_measurement_available": request_input_measurement.available,
        "estimated_request_tokens": estimated_input_tokens,
        "request_estimation_source": estimation_source,
        "context_compacted": bool(getattr(request, "context_compacted", False)),
        "context_limit": getattr(request, "context_limit", None),
    }
    context_limit = summary["context_limit"]
    summary["request_utilization_ratio"] = (
        estimated_input_tokens / context_limit
        if isinstance(context_limit, int) and not isinstance(context_limit, bool) and context_limit > 0
        else None
    )
    stable_config = {
        key: summary[key]
        for key in (
            "model",
            "temperature",
            "max_output_tokens",
            "stream",
            "reasoning_budget",
            "request_contract",
            "structured_mode",
            "structured_contract",
        )
    }
    fingerprint_payload = json.dumps(stable_config, sort_keys=True, separators=(",", ":"), default=str)
    summary["config_fingerprint"] = hashlib.sha256(
        fingerprint_payload.encode("utf-8")
    ).hexdigest()
    return summary
