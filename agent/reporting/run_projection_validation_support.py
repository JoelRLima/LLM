"""Validation-detail projections for run reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from agent.reporting.run_projection_value_support import (
    MAX_PROJECTION_PATH_CHARS,
    _as_mapping,
    _bounded_string_list,
    _freeze,
    _metadata_records,
    _text,
)


def _validation_candidate_records(
    result: Any,
    data: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    result_mapping = _as_mapping(result)
    records: list[Mapping[str, Any]] = []
    for candidate in (
        result_mapping.get("validation_metadata"),
        data.get("validation_metadata"),
        *(_metadata_records(result, data)),
        result_mapping,
        data,
    ):
        if not isinstance(candidate, Mapping):
            continue
        for key in ("validation_metadata", "validation"):
            value = candidate.get(key)
            if isinstance(value, Mapping):
                records.append(value)
        if any(
            key in candidate
            for key in (
                "execution_status",
                "effective_status",
                "tests_requested",
                "test_coverage",
                "plan_fingerprint",
                "selections",
            )
        ):
            records.append(candidate)
    return tuple(records)


def _canonical_validation_detail(history: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Project only the stable validation contract needed by evaluation."""

    for entry in reversed(history):
        result = entry.get("result")
        result_mapping = _as_mapping(result)
        data = result_mapping.get("data")
        data_mapping = data if isinstance(data, Mapping) else {}
        candidates = _validation_candidate_records(result, data_mapping)
        for candidate in candidates:
            if not any(
                key in candidate
                for key in (
                    "execution_status",
                    "effective_status",
                    "tests_requested",
                    "test_coverage",
                    "plan_fingerprint",
                    "selections",
                )
            ):
                continue
            selections: list[dict[str, Any]] = []
            raw_selections = candidate.get("selections")
            if isinstance(raw_selections, Sequence) and not isinstance(
                raw_selections, (str, bytes, bytearray)
            ):
                for raw_selection in list(raw_selections)[:16]:
                    if not isinstance(raw_selection, Mapping):
                        continue
                    targets = _bounded_string_list(
                        raw_selection.get("targets"),
                        limit=16,
                        chars=MAX_PROJECTION_PATH_CHARS,
                    )
                    selections.append(
                        {
                            "command_kind": _text(raw_selection.get("command_kind"), 64)
                            if isinstance(raw_selection.get("command_kind"), str)
                            else None,
                            "scope": _text(raw_selection.get("scope"), 128)
                            if isinstance(raw_selection.get("scope"), str)
                            else None,
                            "targets": targets,
                            "status": _text(raw_selection.get("status"), 64)
                            if isinstance(raw_selection.get("status"), str)
                            else None,
                        }
                    )
            return cast(Mapping[str, Any], _freeze(
                {
                    "execution_status": _text(candidate.get("execution_status"), 64)
                    if isinstance(candidate.get("execution_status"), str)
                    else None,
                    "effective_status": _text(candidate.get("effective_status"), 64)
                    if isinstance(candidate.get("effective_status"), str)
                    else None,
                    "tests_requested": candidate.get("tests_requested")
                    if type(candidate.get("tests_requested")) is bool
                    else False,
                    "test_coverage": _text(candidate.get("test_coverage"), 64)
                    if isinstance(candidate.get("test_coverage"), str)
                    else None,
                    "plan_fingerprint": _text(candidate.get("plan_fingerprint"), 128)
                    if isinstance(candidate.get("plan_fingerprint"), str)
                    else None,
                    "selections": tuple(
                        _freeze(item) for item in selections
                    ),
                }
            ))
    return cast(Mapping[str, Any], _freeze(
        {
            "execution_status": None,
            "effective_status": None,
            "tests_requested": False,
            "test_coverage": None,
            "plan_fingerprint": None,
            "selections": (),
        }
    ))

__all__ = ["_canonical_validation_detail"]
