"""Canonical JSON codec for bounded untrusted context records."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.llm.context_projection import ContextSourceRecord


def _json_mapping(value: Mapping[Any, Any], limit: int, key_limit: int) -> tuple[dict[str, Any], bool]:
    result: dict[str, Any] = {}
    clipped = False
    for raw_key, raw_value in sorted(value.items(), key=lambda pair: str(pair[0])):
        key = str(raw_key)
        if len(key) > key_limit:
            key = key[:key_limit]
            clipped = True
        converted, item_clipped = _json_value(raw_value, string_limit=limit)
        result[key] = converted
        clipped = clipped or item_clipped
    return result, clipped


def _json_sequence(value: Sequence[Any] | set[Any] | frozenset[Any], limit: int) -> tuple[list[Any], bool]:
    converted_items: list[tuple[Any, bool]] = [
        _json_value(item, string_limit=limit) for item in value
    ]
    if isinstance(value, (set, frozenset)):
        converted_items.sort(
            key=lambda item: json.dumps(
                item[0], ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        )
    result_list = [item[0] for item in converted_items]
    clipped = isinstance(value, (set, frozenset)) or any(
        item[1] for item in converted_items
    )
    return result_list, clipped


def _json_scalar(value: Any, limit: int) -> tuple[Any, bool]:
    if isinstance(value, str):
        if len(value) > limit:
            return value[:limit], True
        return value, False
    if value is None or isinstance(value, (bool, int)):
        return value, False
    if isinstance(value, float):
        if math.isfinite(value):
            return value, False
        return str(value), True
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
        if len(text) > limit:
            return text[:limit], True
        return text, True
    converted = str(value)
    if len(converted) > limit:
        converted = converted[:limit]
        return converted, True
    return converted, True


def _json_value(
    value: Any,
    *,
    string_limit: int | None = None,
) -> tuple[Any, bool]:
    """Convert a bounded value to JSON data and clip before serialization."""

    from agent.llm.context_projection import _MAX_CODEC_KEY_CHARS, _MAX_CODEC_STRING_CHARS

    limit = _MAX_CODEC_STRING_CHARS if string_limit is None else string_limit
    if isinstance(value, Mapping):
        return _json_mapping(value, limit, _MAX_CODEC_KEY_CHARS)
    if isinstance(value, (list, tuple, set, frozenset)):
        return _json_sequence(value, limit)
    return _json_scalar(value, limit)


def _coerce_record(
    record: Any,
) -> tuple[dict[str, Any], bool]:
    from agent.llm.context_projection import ContextSourceRecord

    if isinstance(record, ContextSourceRecord):
        raw = record.to_dict()
    elif isinstance(record, Mapping):
        raw = dict(record)
    else:
        raise TypeError("context records must be ContextSourceRecord or mapping")
    normalized, clipped = _json_value(raw)
    if not isinstance(normalized, dict):
        raise TypeError("context record must serialize as an object")
    if bool(normalized.get("truncated")) or clipped:
        normalized["truncated"] = True
        normalized["complete"] = False
    return normalized, clipped


def render_untrusted_context_envelope(
    records: Sequence[Any],
    *,
    category: str | None = None,
) -> str:
    """Render one complete canonical JSON untrusted-data envelope."""

    from agent.llm.context_projection import (
        ENVELOPE_NOTICE,
        ENVELOPE_SCHEMA,
        OPTIONAL_AUXILIARY,
        REQUIRED_EVIDENCE,
    )

    if not records:
        return ""
    serialized_records: list[dict[str, Any]] = []
    clipped = False
    for record in records:
        normalized, was_clipped = _coerce_record(record)
        serialized_records.append(normalized)
        clipped = clipped or was_clipped
    effective_category = category or (
        REQUIRED_EVIDENCE
        if any(item.get("necessity") == REQUIRED_EVIDENCE for item in serialized_records)
        else OPTIONAL_AUXILIARY
    )
    envelope: dict[str, Any] = {
        "schema": ENVELOPE_SCHEMA,
        "schema_version": 1,
        "data_class": effective_category,
        "notice": ENVELOPE_NOTICE,
        "records": serialized_records,
    }
    if clipped:
        envelope["records_clipped_before_serialization"] = True
    return json.dumps(
        envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def estimated_tokens(text: str | None) -> int:
    from agent.llm.context_projection import CHARS_PER_ESTIMATED_TOKEN

    if not text:
        return 0
    return max(
        1,
        (len(text) + CHARS_PER_ESTIMATED_TOKEN - 1) // CHARS_PER_ESTIMATED_TOKEN,
    )


def record_from_mapping(
    source_id: str,
    source_kind: str,
    data: Mapping[str, Any],
    *,
    necessity: str | None = None,
    trust_class: str | None = None,
    reason: str | None = None,
    identity: str | None = None,
    freshness: str | None = None,
    truncated: bool = False,
    complete: bool = True,
) -> ContextSourceRecord:
    from agent.llm.context_projection import (
        OPTIONAL_AUXILIARY,
        UNTRUSTED_SESSION,
        ContextSourceRecord,
    )

    effective_necessity = OPTIONAL_AUXILIARY if necessity is None else necessity
    effective_trust_class = UNTRUSTED_SESSION if trust_class is None else trust_class
    effective_reason = "selected as bounded auxiliary data" if reason is None else reason

    rough = json.dumps(dict(data), ensure_ascii=False, default=str, separators=(",", ":"))
    return ContextSourceRecord(
        source_id=source_id,
        source_kind=source_kind,
        necessity=effective_necessity,
        trust_class=effective_trust_class,
        reason=effective_reason,
        estimated_tokens=estimated_tokens(rough),
        truncated=truncated,
        complete=complete,
        data=dict(data),
        identity=identity,
        freshness=freshness,
    )


__all__ = [
    "_coerce_record",
    "_json_value",
    "estimated_tokens",
    "record_from_mapping",
    "render_untrusted_context_envelope",
]
