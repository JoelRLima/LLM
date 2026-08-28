"""Canonical typed projection of one task's model/tool measurements."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from agent.reporting.metrics_support import (
    complete_token_count as _complete_token_count,
)
from agent.reporting.metrics_support import (
    entry_accounted_tokens as _entry_accounted_tokens,
)
from agent.reporting.metrics_support import (
    entry_usage_complete as _entry_usage_complete,
)
from agent.reporting.metrics_support import (
    first_number as _first_number,
)
from agent.reporting.metrics_support import (
    has_number as _has_number,
)
from agent.reporting.metrics_support import (
    metric_type as _metric_type,
)
from agent.reporting.metrics_support import (
    snapshot_number as _snapshot_number,
)
from agent.reporting.metrics_support import (
    snapshot_value as _snapshot_value,
)
from agent.reporting.metrics_support import (
    token_count as _token_count,
)
from agent.reporting.request_metrics import project_request_input_metrics

TOKEN_KEYS = ("tokens", "total_tokens", "token_count", "prompt_tokens", "completion_tokens")
DURATION_KEYS = ("duration_ms", "elapsed_ms", "latency_ms")
MODEL_CALL_TYPES = ("model_call", "llm_call", "completion")


@dataclass(frozen=True, slots=True)
class RunMetricsSnapshot:
    """Read-only reconciliation of provider, budget, and run measurements.

    ``reported_*`` fields are provider observations, ``derived_tokens`` is
    calculated only from complete provider parts, ``estimated_tokens`` is a
    fallback estimate, and ``accounted_tokens`` is the budget ledger value.
    ``reserved_tokens`` is the active preflight reservation; the allowance
    total is retained separately as historical per-call telemetry.
    """

    total_tokens: int | None
    reported_tokens: int | None
    reported_input_tokens: int
    reported_output_tokens: int
    reported_total_tokens: int
    request_input_tokens: int | None
    request_input_measurement_source: str
    request_input_measurement_exact: bool | None
    request_input_measurement_available: bool
    request_input_token_delta: int | None
    request_input_token_abs_delta: int | None
    request_input_token_consistent: bool | None
    derived_tokens: int | None
    reserved_tokens: int
    reserved_allowance_tokens: int
    estimated_tokens: int
    accounted_tokens: int
    token_usage_complete: bool
    token_measurement: str
    total_duration_ms: int
    duration_available: bool
    model_calls: int
    run_calls: int
    tool_calls: int
    tools_called: int
    history_records: int
    token_usage_available: bool
    historical_token_fallback: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def project_run_metrics(
    entries: Sequence[Mapping[str, Any]] | None,
    tools_called: int | None = None,
    *,
    tool_calls: int | None = None,
    history_records: int | None = None,
    budget_snapshot: Any = None,
) -> RunMetricsSnapshot:
    """Reconcile one canonical metric stream into a typed snapshot."""

    valid_entries = [entry for entry in (entries or ()) if isinstance(entry, Mapping)]
    model_entries = [entry for entry in valid_entries if _metric_type(entry) == "model_call"]
    run_entries = [entry for entry in valid_entries if _metric_type(entry) == "run"]
    historical_entries = [
        entry for entry in valid_entries if _metric_type(entry) == "model_metadata"
    ]
    historical_fallback = not model_entries and any(
        _has_number(entry, TOKEN_KEYS) for entry in historical_entries
    )
    token_entries = historical_entries if historical_fallback else model_entries
    token_values = [
        _token_count(entry) for entry in token_entries if _has_number(entry, TOKEN_KEYS)
    ]
    complete_flags = [_entry_usage_complete(entry) for entry in model_entries]
    token_usage_complete = bool(model_entries) and all(complete_flags)
    reported_input_tokens = sum(
        _first_number(entry, ("input_tokens", "prompt_tokens"))
        for entry in model_entries
    )
    reported_output_tokens = sum(
        _first_number(entry, ("output_tokens", "completion_tokens"))
        for entry in model_entries
    )
    reported_total_tokens = sum(
        _first_number(entry, ("total_tokens",))
        for entry in model_entries
        if _has_number(entry, ("total_tokens",))
    )
    request_input = project_request_input_metrics(model_entries)
    request_input_tokens = request_input["request_input_tokens"]
    request_input_source = request_input["request_input_measurement_source"]
    request_input_exact = request_input["request_input_measurement_exact"]
    estimated_tokens = sum(
        _first_number(entry, ("estimated_tokens",)) for entry in model_entries
    )
    reserved_allowance_tokens = sum(
        _first_number(entry, ("reserved_tokens",)) for entry in model_entries
    )
    accounted_tokens = sum(_entry_accounted_tokens(entry) for entry in model_entries)
    all_model_totals = bool(model_entries) and all(
        _has_number(entry, ("total_tokens",)) for entry in model_entries
    )
    derived_total_tokens = sum(_complete_token_count(entry) for entry in model_entries)

    if historical_fallback:
        total_tokens: int | None = sum(token_values) if token_values else None
        reported_tokens: int | None = total_tokens
    elif token_usage_complete:
        total_tokens = reported_total_tokens if all_model_totals else derived_total_tokens
        reported_tokens = total_tokens
    else:
        total_tokens = None
        reported_tokens = reported_total_tokens if reported_total_tokens else None

    actual_tool_calls = tool_calls if tool_calls is not None else tools_called
    actual_tool_calls = int(actual_tool_calls or 0)
    history_records = int(history_records or 0)
    model_calls = (
        _snapshot_number(budget_snapshot, "model_calls", len(model_entries))
        if budget_snapshot is not None
        else len(model_entries)
    )
    snapshot_total_calls = (
        _snapshot_number(budget_snapshot, "model_calls_with_reported_total", 0)
        if budget_snapshot is not None
        else 0
    )
    snapshot_reports_all_totals = bool(
        budget_snapshot is not None
        and model_calls > 0
        and snapshot_total_calls == model_calls
    )
    reserved_tokens = reserved_allowance_tokens

    if budget_snapshot is not None:
        actual_tool_calls = _snapshot_number(
            budget_snapshot, "tool_calls", actual_tool_calls
        )
        reported_input_tokens = _snapshot_number(
            budget_snapshot, "reported_input_tokens", reported_input_tokens
        )
        reported_output_tokens = _snapshot_number(
            budget_snapshot, "reported_output_tokens", reported_output_tokens
        )
        reported_total_tokens = _snapshot_number(
            budget_snapshot, "reported_total_tokens", reported_total_tokens
        )
        estimated_tokens = _snapshot_number(
            budget_snapshot, "estimated_tokens", estimated_tokens
        )
        accounted_tokens = _snapshot_number(
            budget_snapshot, "accounted_tokens", accounted_tokens
        )
        reserved_tokens = _snapshot_number(
            budget_snapshot, "reserved_tokens", reserved_tokens
        )
        token_usage_complete = bool(
            _snapshot_value(
                budget_snapshot, "token_usage_complete", token_usage_complete
            )
        )
        if model_calls == 0:
            total_tokens = 0
            reported_tokens = 0
        elif token_usage_complete:
            total_tokens = (
                reported_total_tokens
                if all_model_totals
                or (not model_entries and snapshot_total_calls == model_calls)
                else derived_total_tokens
                if model_entries
                else reported_input_tokens + reported_output_tokens
            )
            reported_tokens = total_tokens
        else:
            total_tokens = None
            reported_tokens = reported_total_tokens or None

    if historical_fallback:
        derived_tokens = None
        token_measurement = "unavailable"
    elif token_usage_complete and (all_model_totals or snapshot_reports_all_totals):
        derived_tokens = None
        token_measurement = "provider_reported"
    elif token_usage_complete:
        derived_tokens = total_tokens
        token_measurement = "derived"
    elif estimated_tokens > 0 or reported_total_tokens > 0 or token_values:
        derived_tokens = None
        token_measurement = "estimated"
    else:
        derived_tokens = None
        token_measurement = "unavailable"

    duration_entries = run_entries or model_entries
    duration = sum(_first_number(entry, DURATION_KEYS) for entry in duration_entries)
    return RunMetricsSnapshot(
        total_tokens=total_tokens,
        reported_tokens=reported_tokens,
        reported_input_tokens=reported_input_tokens,
        reported_output_tokens=reported_output_tokens,
        reported_total_tokens=reported_total_tokens,
        request_input_tokens=request_input_tokens,
        request_input_measurement_source=request_input_source,
        request_input_measurement_exact=request_input_exact,
        request_input_measurement_available=request_input_tokens is not None,
        request_input_token_delta=request_input["request_input_token_delta"],
        request_input_token_abs_delta=request_input["request_input_token_abs_delta"],
        request_input_token_consistent=request_input["request_input_token_consistent"],
        derived_tokens=derived_tokens,
        reserved_tokens=reserved_tokens,
        reserved_allowance_tokens=reserved_allowance_tokens,
        estimated_tokens=estimated_tokens,
        accounted_tokens=accounted_tokens,
        token_usage_complete=token_usage_complete,
        token_measurement=token_measurement,
        total_duration_ms=duration,
        duration_available=bool(
            duration_entries
            and any(_has_number(entry, DURATION_KEYS) for entry in duration_entries)
        ),
        model_calls=model_calls,
        run_calls=len(run_entries),
        tool_calls=actual_tool_calls,
        tools_called=actual_tool_calls,
        history_records=history_records,
        token_usage_available=bool(token_usage_complete)
        or bool(token_values)
        or bool(estimated_tokens)
        or bool(reported_input_tokens or reported_output_tokens),
        historical_token_fallback=historical_fallback,
    )


__all__ = [
    "DURATION_KEYS",
    "MODEL_CALL_TYPES",
    "RunMetricsSnapshot",
    "TOKEN_KEYS",
    "project_run_metrics",
]
