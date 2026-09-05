"""Context-budget fitting for the canonical untrusted projection."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from agent.llm.context_projection import (
    CHARS_PER_ESTIMATED_TOKEN,
    MAX_OPTIONAL_TOKENS,
    OPTIONAL_AUXILIARY,
    REQUIRED_EVIDENCE,
    ContextRequestFit,
    ContextSourceRecord,
    ModelContextProjection,
)
from agent.llm.context_projection_codec import (
    _json_value,
    estimated_tokens,
    render_untrusted_context_envelope,
)
from agent.runtime.request_measurement import (
    RequestInputMeasurement,
    measure_model_request_input_tokens,
)


def _excluded_optional_records(
    records: Sequence[ContextSourceRecord],
    reason: str,
) -> tuple[ContextSourceRecord, ...]:
    return tuple(replace(record, included=False, reason=reason) for record in records)


def _clip_record_for_budget(
    record: ContextSourceRecord,
    allowance_chars: int,
) -> ContextSourceRecord:
    bounded, clipped = _json_value(
        dict(record.data),
        string_limit=max(0, allowance_chars),
    )
    data = bounded if isinstance(bounded, dict) else {"content": str(bounded)}
    return replace(
        record,
        data=dict(data),
        truncated=record.truncated or clipped,
        complete=record.complete and not clipped,
    )


def _fit_optional_records(
    records: Sequence[ContextSourceRecord],
    budget_tokens: int,
) -> tuple[tuple[ContextSourceRecord, ...], str | None, bool]:
    if not records or budget_tokens <= 0:
        return _excluded_optional_records(records, "optional_budget_zero"), None, False
    active = tuple(record for record in records if record.included)
    if not active:
        return (), None, False
    budget_chars = budget_tokens * CHARS_PER_ESTIMATED_TOKEN
    full_message = render_untrusted_context_envelope(
        active,
        category=OPTIONAL_AUXILIARY,
    )
    if len(full_message) <= budget_chars:
        return active, full_message, False

    clipped_records: list[ContextSourceRecord] = []
    remaining = budget_chars
    for index, record in enumerate(active):
        remaining_records = len(active) - index
        share = remaining // remaining_records if remaining_records else 0
        bounded = _clip_record_for_budget(record, share)
        clipped_records.append(bounded)
        rendered = json.dumps(
            dict(bounded.data),
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )
        remaining = max(0, remaining - len(rendered))
    candidate_message = render_untrusted_context_envelope(
        clipped_records,
        category=OPTIONAL_AUXILIARY,
    )
    if candidate_message and len(candidate_message) <= budget_chars:
        return tuple(clipped_records), candidate_message, True
    return _excluded_optional_records(active, "optional_candidate_overflow"), None, True


def fit_contextual_request(
    *,
    mandatory_request: Any,
    required_records: Sequence[ContextSourceRecord] = (),
    optional_records: Sequence[ContextSourceRecord] = (),
    context_limit: int | None,
    gateway: Any,
    build_request: Callable[[str | None, str | None], Any],
) -> ContextRequestFit:
    """Choose a request-local projection using the existing measurement owner."""

    required_message = (
        render_untrusted_context_envelope(required_records, category=REQUIRED_EVIDENCE)
        if required_records
        else None
    )
    if required_message:
        mandatory_request = build_request(required_message, None)
    measurement = measure_model_request_input_tokens(mandatory_request, gateway)
    output_reserve = getattr(mandatory_request, "max_output_tokens", 0)
    if isinstance(output_reserve, bool) or not isinstance(output_reserve, int):
        output_reserve = 0
    known_limit = context_limit if isinstance(context_limit, int) and context_limit > 0 else None
    mandatory_tokens = measurement.token_count if measurement.available else None
    if known_limit is not None and measurement.exact and mandatory_tokens is not None:
        if mandatory_tokens + output_reserve > known_limit:
            excluded = _excluded_optional_records(optional_records, "mandatory_overflow")
            projection = ModelContextProjection(
                required_message,
                None,
                tuple(required_records) + excluded,
                estimated_tokens(required_message),
                False,
                False,
            )
            return ContextRequestFit(
                mandatory_request,
                projection,
                measurement,
                final_measurement=measurement,
                mandatory_overflow=True,
            )
        available_optional = max(0, known_limit - output_reserve - mandatory_tokens)
        optional_budget = max(
            0,
            min(MAX_OPTIONAL_TOKENS, known_limit // 4, available_optional),
        )
        fit_proven = True
    elif known_limit is not None:
        optional_budget = 0
        fit_proven = False
    else:
        optional_budget = MAX_OPTIONAL_TOKENS
        fit_proven = False

    selected_optional, optional_message, optional_truncated = _fit_optional_records(
        optional_records,
        optional_budget,
    )
    final_measurement: RequestInputMeasurement | None = None
    chosen_request = mandatory_request
    if optional_message:
        candidate_request = build_request(required_message, optional_message)
        final_measurement = measure_model_request_input_tokens(candidate_request, gateway)
        if (
            known_limit is not None
            and final_measurement.exact
            and final_measurement.token_count is not None
            and final_measurement.token_count + output_reserve > known_limit
        ):
            optional_message = None
            optional_truncated = True
            selected_optional = _excluded_optional_records(
                optional_records,
                "final_exact_fit_overflow",
            )
        else:
            chosen_request = candidate_request
    if not optional_message and not selected_optional:
        selected_optional = _excluded_optional_records(
            optional_records,
            "optional_not_included",
        )
    projection = ModelContextProjection(
        required_message,
        optional_message,
        tuple(required_records) + tuple(selected_optional),
        estimated_tokens(required_message) + estimated_tokens(optional_message),
        optional_truncated,
        fit_proven,
    )
    return ContextRequestFit(
        chosen_request,
        projection,
        measurement,
        final_measurement=final_measurement,
        mandatory_overflow=False,
    )


__all__ = ["fit_contextual_request"]
