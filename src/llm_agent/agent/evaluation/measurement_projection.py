"""Bounded projection of runtime measurement metadata for evaluation output."""

from __future__ import annotations


def _bounded(value: object, limit: int = 500) -> str:
    return str(value or "")[:limit]


def project_measurement_summary(measurement: dict[str, object]) -> dict[str, object]:
    allowed = {
        "task_id", "runtime_task_id", "root_task_id", "correlation", "duration_ms", "tools", "invocation_id", "invocation_ids", "invocations",
        "terminal_outcome", "error", "output_chars", "truncated", "tool_history_count", "tool_calls", "model_calls", "gateway_calls", "run_id", "status",
        "estimated_tokens", "accounted_tokens", "reserved_tokens", "token_usage_complete",
        "reported_input_tokens", "reported_output_tokens", "reported_total_tokens", "request_input_tokens", "request_input_measurement_source", "request_input_measurement_exact", "request_input_measurement_available", "request_input_token_delta", "request_input_token_abs_delta", "request_input_token_consistent", "total_tokens", "token_measurement",
        "provider_identity", "declared_provider_identity", "observed_provider_identity",
    }
    result: dict[str, object] = {}
    for key in allowed:
        if key not in measurement:
            continue
        value = measurement[key]
        if key in {"error", "task_id", "runtime_task_id", "root_task_id", "terminal_outcome", "status"}:
            result[key] = _bounded(value)
        elif key == "correlation" and isinstance(value, dict):
            result[key] = {
                name: _bounded(item, 200) if item is not None else None
                for name, item in value.items()
                if name in {"run_id", "root_task_id", "task_id", "parent_task_id", "node_id"}
            }
        elif key in {"tools", "invocation_ids"} and isinstance(value, (list, tuple)):
            result[key] = [_bounded(item, 200) for item in value]
        elif key == "invocations" and isinstance(value, (list, tuple)):
            result[key] = [
                {
                    "invocation_id": _bounded(item.get("invocation_id"), 200),
                    "outcome": _bounded(item.get("outcome"), 100),
                }
                for item in value
                if isinstance(item, dict)
            ]
        else:
            result[key] = value
    return result


__all__ = ["project_measurement_summary"]
