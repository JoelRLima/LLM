"""Bounded response observations, never a verdict about task success or cause."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from llm_agent.agent.llm.decision_contract import (
    admit_model_decision_value,
    request_contract_value,
    supports_model_decision_contract,
)
from llm_agent.agent.llm.structured_output import StructuredOutputError, extract_json_value

MAX_ASSESSMENT_CHARS = 64_000


def _signal_code(record: Mapping[str, Any]) -> str | None:
    if record.get("stream_error_observed") is True:
        return "MODEL_STREAM_ERROR"
    if record.get("cancelled") is True:
        return "MODEL_STREAM_CONSUMER_CLOSED"
    if record.get("provider_call_succeeded") is False:
        return "MODEL_CALL_FAILED"
    if record.get("finish_reason") == "content_filter":
        return "MODEL_CONTENT_FILTERED"
    if record.get("finish_reason") == "length":
        return "MODEL_OUTPUT_INCOMPLETE"
    if record.get("stream_done_observed") is False:
        return "MODEL_STREAM_TERMINATION_UNOBSERVED"
    return None


def _response_code(record: Mapping[str, Any], contract: str | None) -> str:
    signal = _signal_code(record)
    if signal is not None:
        return signal
    if contract is None:
        return "MODEL_CONTRACT_UNAVAILABLE"
    if not supports_model_decision_contract(contract):
        return "MODEL_CONTRACT_NOT_ASSESSED"
    response = record.get("response")
    if not isinstance(response, str):
        return "MODEL_RESPONSE_UNAVAILABLE"
    if len(response) > MAX_ASSESSMENT_CHARS:
        return "MODEL_ASSESSMENT_LIMIT"
    try:
        value = extract_json_value(response)
        admitted = admit_model_decision_value(value, request_contract=contract)
    except StructuredOutputError:
        return "MODEL_JSON_INVALID"
    except RecursionError:
        return "MODEL_ASSESSMENT_LIMIT"
    return "MODEL_CONTRACT_VALID" if admitted is not None else "MODEL_CONTRACT_INVALID"


def diagnose_model_call(record: Mapping[str, Any]) -> dict[str, Any]:
    """Read carried contract and recorded signals without guessing from prompts."""
    request = record.get("request")
    contract = request_contract_value(request.get("request_contract")) if isinstance(request, Mapping) else None
    return {
        "version": 1,
        "code": _response_code(record, contract),
        "request_contract": contract,
        "contract_source": "request_contract" if contract is not None else "unavailable",
        "scope": "response_contract_only",
        "task_success": None,
    }


def summarize_call_diagnostics(records: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(
        str(item["diagnostic"]["code"]) for item in records
        if isinstance(item.get("diagnostic"), Mapping) and isinstance(item["diagnostic"].get("code"), str)
    ).items()))


def summarize_variant_diagnostics(rows: list[dict[str, Any]], variant: str) -> dict[str, int]:
    calls: list[dict[str, Any]] = []
    for row in rows:
        context = row.get("stage_context")
        if row.get("variant") == variant and isinstance(context, list):
            calls.extend(item for item in context if isinstance(item, dict))
    return summarize_call_diagnostics(calls)
