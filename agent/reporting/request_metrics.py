"""Request-input fields for the canonical run-metrics projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.reporting.metrics_support import first_number, has_number
from agent.runtime.budget_estimation import UNAVAILABLE


def project_request_input_metrics(
    model_entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate measured request input without changing provider usage."""

    entries = [
        entry
        for entry in model_entries
        if has_number(entry, ("request_input_tokens",))
        or (
            has_number(entry, ("estimated_request_tokens",))
            and str(
                entry.get(
                    "request_input_measurement_source",
                    entry.get("request_estimation_source", UNAVAILABLE),
                )
            )
            != UNAVAILABLE
        )
    ]
    sources = [
        str(
            entry.get(
                "request_input_measurement_source",
                entry.get("request_estimation_source", UNAVAILABLE),
            )
        )
        for entry in entries
    ]
    source = (
        sources[0]
        if sources and all(item == sources[0] for item in sources)
        else "mixed"
        if sources
        else UNAVAILABLE
    )
    deltas = [
        first_number(entry, ("request_input_token_delta",))
        for entry in model_entries
        if has_number(entry, ("request_input_token_delta",))
    ]
    absolute_deltas = [
        first_number(entry, ("request_input_token_abs_delta",))
        for entry in model_entries
        if has_number(entry, ("request_input_token_abs_delta",))
    ]
    consistency = [
        entry.get("request_input_token_consistent")
        for entry in model_entries
        if isinstance(entry.get("request_input_token_consistent"), bool)
    ]
    return {
        "request_input_tokens": (
            sum(first_number(entry, ("request_input_tokens", "estimated_request_tokens")) for entry in entries)
            if entries
            else None
        ),
        "request_input_measurement_source": source,
        "request_input_measurement_exact": (
            all(bool(entry.get("request_input_measurement_exact", False)) for entry in entries)
            if entries
            else None
        ),
        "request_input_token_delta": sum(deltas) if deltas else None,
        "request_input_token_abs_delta": sum(absolute_deltas) if absolute_deltas else None,
        "request_input_token_consistent": all(consistency) if consistency else None,
    }


__all__ = ["project_request_input_metrics"]
