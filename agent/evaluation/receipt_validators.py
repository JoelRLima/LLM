"""Low-level field validators for EvaluationReceiptV1 construction.

This module must NOT import from receipt.py at module level to avoid circular
imports.  Callers are responsible for providing the error factory.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Callable, TypedDict

from agent.variants.models import (
    CompositionPurpose,
    VariantComposition,
    VariantLifecycle,
    VariantSeam,
    VariantSelection,
)
from agent.variants.preflight import validate_variant_composition

_MAX_CHANGED_FILES = 128
_MAX_CHANGED_FILE_CHARS = 512

# Type alias: callable that converts a message to the appropriate ValueError subclass.
_ErrorFactory = Callable[[str], Exception]


class _RawRun(TypedDict):
    run_id: str
    root_task_id: str
    task_id: str | None
    terminal_status: str


class _RawVariant(TypedDict):
    profile_id: str
    fingerprint: str
    composition: Mapping[str, object]


class _RawMeasurements(TypedDict):
    duration_ms: int
    model_calls: int
    tool_calls: int
    tool_history_count: int
    accounted_tokens: int
    reported_input_tokens: int
    reported_output_tokens: int
    reported_total_tokens: int
    token_usage_complete: bool
    output_chars: int
    output_truncated: bool
    changed_files: tuple[str, ...]
    validation_status: str | None
    rollback_occurred: bool
    replan_count: int


class _RawTechnical(TypedDict):
    runtime_success: bool
    evaluator_passed: bool
    evaluator_failure_codes: tuple[str, ...]


class _RawReceipt(TypedDict):
    schema_version: int
    receipt_id: str
    experiment_id: str
    trial_id: str
    scenario_id: str
    scenario_arm_id: str
    repetition: int
    attempt: int
    evidence_level: str
    run: _RawRun
    variant: _RawVariant
    measurements: _RawMeasurements
    technical: _RawTechnical


def _raw_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an object")
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{label} keys must be strings")
        normalized[key] = item
    return normalized


def _raw_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    return value


def _raw_optional_text(value: object, label: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{label} must be a string or null")
    return value


def _raw_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    return value


def _raw_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{label} must be a boolean")
    return value


def _raw_text_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{label} must be a sequence")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TypeError(f"{label} must contain strings")
        normalized.append(item)
    return tuple(normalized)


def _narrow_raw_receipt(value: object) -> _RawReceipt:
    raw = _raw_mapping(value, "receipt")
    run = _raw_mapping(raw.get("run"), "run")
    variant = _raw_mapping(raw.get("variant"), "variant")
    measurements = _raw_mapping(raw.get("measurements"), "measurements")
    technical = _raw_mapping(raw.get("technical"), "technical")
    return {
        "schema_version": _raw_int(raw.get("schema_version"), "schema_version"),
        "receipt_id": _raw_text(raw.get("receipt_id"), "receipt_id"),
        "experiment_id": _raw_text(raw.get("experiment_id"), "experiment_id"),
        "trial_id": _raw_text(raw.get("trial_id"), "trial_id"),
        "scenario_id": _raw_text(raw.get("scenario_id"), "scenario_id"),
        "scenario_arm_id": _raw_text(raw.get("scenario_arm_id"), "scenario_arm_id"),
        "repetition": _raw_int(raw.get("repetition"), "repetition"),
        "attempt": _raw_int(raw.get("attempt"), "attempt"),
        "evidence_level": _raw_text(raw.get("evidence_level"), "evidence_level"),
        "run": {
            "run_id": _raw_text(run.get("run_id"), "run.run_id"),
            "root_task_id": _raw_text(run.get("root_task_id"), "run.root_task_id"),
            "task_id": _raw_optional_text(run.get("task_id"), "run.task_id"),
            "terminal_status": _raw_text(run.get("terminal_status"), "run.terminal_status"),
        },
        "variant": {
            "profile_id": _raw_text(variant.get("profile_id"), "variant.profile_id"),
            "fingerprint": _raw_text(variant.get("fingerprint"), "variant.fingerprint"),
            "composition": _raw_mapping(variant.get("composition"), "variant.composition"),
        },
        "measurements": {
            "duration_ms": _raw_int(measurements.get("duration_ms"), "measurements.duration_ms"),
            "model_calls": _raw_int(measurements.get("model_calls"), "measurements.model_calls"),
            "tool_calls": _raw_int(measurements.get("tool_calls"), "measurements.tool_calls"),
            "tool_history_count": _raw_int(
                measurements.get("tool_history_count"), "measurements.tool_history_count"
            ),
            "accounted_tokens": _raw_int(measurements.get("accounted_tokens"), "measurements.accounted_tokens"),
            "reported_input_tokens": _raw_int(
                measurements.get("reported_input_tokens"), "measurements.reported_input_tokens"
            ),
            "reported_output_tokens": _raw_int(
                measurements.get("reported_output_tokens"), "measurements.reported_output_tokens"
            ),
            "reported_total_tokens": _raw_int(
                measurements.get("reported_total_tokens"), "measurements.reported_total_tokens"
            ),
            "token_usage_complete": _raw_bool(
                measurements.get("token_usage_complete"), "measurements.token_usage_complete"
            ),
            "output_chars": _raw_int(measurements.get("output_chars"), "measurements.output_chars"),
            "output_truncated": _raw_bool(
                measurements.get("output_truncated"), "measurements.output_truncated"
            ),
            "changed_files": _raw_text_tuple(measurements.get("changed_files"), "measurements.changed_files"),
            "validation_status": _raw_optional_text(
                measurements.get("validation_status"), "measurements.validation_status"
            ),
            "rollback_occurred": _raw_bool(
                measurements.get("rollback_occurred"), "measurements.rollback_occurred"
            ),
            "replan_count": _raw_int(measurements.get("replan_count"), "measurements.replan_count"),
        },
        "technical": {
            "runtime_success": _raw_bool(technical.get("runtime_success"), "technical.runtime_success"),
            "evaluator_passed": _raw_bool(technical.get("evaluator_passed"), "technical.evaluator_passed"),
            "evaluator_failure_codes": _raw_text_tuple(
                technical.get("evaluator_failure_codes"), "technical.evaluator_failure_codes"
            ),
        },
    }


def composition_from_dict(value: object, invalid: _ErrorFactory) -> VariantComposition:
    """Parse and validate a VariantComposition from a raw mapping.

    *invalid* must be a callable(message) -> Exception that raises when called.
    """
    if not isinstance(value, Mapping):
        raise invalid("variant composition must be an object")
    try:
        schema_version = value["schema_version"]
        purpose = CompositionPurpose(str(value["purpose"]))
        experiment_id = value.get("experiment_id")
        raw_selections = value["selections"]
        if not isinstance(raw_selections, list):
            raise TypeError("selections must be a list")
        selections = tuple(
            VariantSelection(
                seam=VariantSeam(str(item["seam"])),
                variant_id=str(item["variant_id"]),
                lifecycle=VariantLifecycle(str(item["lifecycle"])),
                contract_version=item.get("contract_version", 1),
            )
            for item in raw_selections
            if isinstance(item, Mapping)
        )
        if len(selections) != len(raw_selections):
            raise TypeError("invalid selection")
        composition = VariantComposition(
            schema_version=schema_version,
            purpose=purpose,
            selections=selections,
            experiment_id=experiment_id,
        )
        return validate_variant_composition(composition)
    except ValueError:
        raise
    except Exception as exc:
        raise invalid("variant composition is invalid") from exc


def validate_changed_files(value: object, invalid: _ErrorFactory) -> tuple[str, ...]:
    """Validate the changed_files sequence: bounded, normalized, sorted, unique."""
    if not isinstance(value, (list, tuple)):
        raise invalid("changed_files must be a sequence")
    if len(value) > _MAX_CHANGED_FILES:
        raise invalid("changed_files exceeds its bound")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > _MAX_CHANGED_FILE_CHARS:
            raise invalid("changed file path is invalid")
        if "\\" in item or item.startswith("/") or re.match(r"^[A-Za-z]:", item):
            raise invalid("changed file path must be relative and use forward slashes")
        parts = item.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise invalid("changed file path is not normalized")
        result.append(item)
    if tuple(result) != tuple(sorted(result)) or len(set(result)) != len(result):
        raise invalid("changed_files must be sorted and unique")
    return tuple(result)


__all__ = [
    "composition_from_dict",
    "validate_changed_files",
]
