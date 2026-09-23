"""Canonical Engineering summary and reference validation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

from agent.engineering.contracts import (
    EngineeringEnvironmentV1,
    EngineeringErrorV1,
    EngineeringExecutionContext,
    EngineeringQueryStatus,
    EngineeringReferenceV1,
    EngineeringRunPhase,
    EngineeringRunResultV1,
    EngineeringRunSummaryV1,
    EngineeringStoreError,
    EngineeringTerminalStatus,
    EngineeringTransitionMode,
    canonical_json_bytes,
    order_reason_codes,
    parse_canonical_utc,
    validate_operation_id,
    validate_run_id,
    validate_sha256,
    validate_workspace_id,
)
from agent.runtime.filesystem_primitives import inspect_final_path
from agent.runtime.process_identity import current_process_start_id

MAX_ENGINEERING_SUMMARY_BYTES = 32_768
MAX_ENGINEERING_SUMMARY_DEPTH = 6
MAX_ENGINEERING_SUMMARY_ITEMS = 64
MAX_ENGINEERING_SUMMARY_STRING_BYTES = 8_192
MAX_ENGINEERING_SUMMARY_KEY_BYTES = 128
MAX_ENGINEERING_REFERENCES = 64


class EngineeringRecordError(RuntimeError):
    """Raised when canonical durable Engineering evidence is unsafe or corrupt."""


def canonical_document_text(document: dict[str, Any]) -> str:
    return canonical_json_bytes(document).decode("utf-8") + "\n"


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _bounded_document(path: Path, limit: int) -> tuple[dict[str, Any], bytes, os.stat_result]:
    inspection = inspect_final_path(path)
    metadata = inspection.metadata
    if not inspection.exists or inspection.is_link_like or metadata is None or not stat.S_ISREG(metadata.st_mode):
        raise EngineeringRecordError("unsafe record")
    if metadata.st_size > limit:
        raise EngineeringRecordError("oversize record")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise EngineeringRecordError("oversize record")
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise EngineeringRecordError("invalid JSON") from exc
    if not isinstance(document, dict) or canonical_document_text(document).encode("utf-8") != raw:
        raise EngineeringRecordError("noncanonical record")
    named_run = re.fullmatch(r"(engr-[0-9a-f]{32})(?:\.pending)?\.json", path.name)
    if named_run is not None and document.get("run_id") != named_run.group(1):
        raise EngineeringRecordError("path/content run_id mismatch")
    return document, raw, metadata


def read_run_records(paths: Any, run_pattern: Any, limit: int) -> list[tuple[dict[str, Any], bytes, os.stat_result]]:
    records = []
    for entry in paths.engineering_runs_dir.iterdir():
        if not run_pattern.fullmatch(entry.name):
            raise EngineeringRecordError("foreign run entry")
        records.append(_bounded_document(entry, limit))
    return records


def active_from_transition(marker: dict[str, Any]) -> dict[str, Any]:
    return {key: marker[key] for key in ("run_id", "operation_id", "started_at_utc", "workspace_id", "environment", "resource_identity_fingerprint")}


def load_transition_record(paths: Any, marker_path: Path, transition_limit: int, run_limit: int, identity: Callable[[dict[str, Any], dict[str, Any]], bool]) -> tuple[dict[str, Any], dict[str, Any] | None, bytes, EngineeringRunSummaryV1 | None]:
    marker, _, _ = _bounded_document(marker_path, transition_limit)
    run_id = validate_transition(marker)
    run_path = paths.engineering_run_file(run_id)
    if not run_path.exists():
        if marker.get("mode") == EngineeringTransitionMode.TERMINAL_PENDING.value:
            return marker, None, b"", None
        raise EngineeringStoreError("ENGINEERING_STORE_STATE_UNSAFE")
    run, raw, _ = _bounded_document(run_path, run_limit)
    summary = _summary_from_record(run)
    if not identity(marker, run):
        raise EngineeringStoreError("ENGINEERING_STORE_STATE_UNSAFE")
    return marker, run, raw, summary


def _environment(document: object) -> EngineeringEnvironmentV1:
    if not isinstance(document, dict) or set(document) != {"schema_version", "workspace_id", "candidate_identity"}:
        raise EngineeringRecordError("invalid environment")
    try:
        return EngineeringEnvironmentV1(document["workspace_id"], document["candidate_identity"], document["schema_version"])
    except (TypeError, ValueError) as exc:
        raise EngineeringRecordError("invalid environment") from exc


def _summary_from_record(document: dict[str, Any]) -> EngineeringRunSummaryV1:
    phase = document.get("phase")
    expected_active = {"schema_version", "run_id", "operation_id", "phase", "status", "reason_codes", "started_at_utc", "finished_at_utc", "duration_ms", "workspace_id", "environment", "owner_pid", "owner_process_start_id", "resource_identity_fingerprint", "persisted"}
    expected_terminal = {"schema_version", "run_id", "operation_id", "phase", "status", "reason_codes", "started_at_utc", "finished_at_utc", "duration_ms", "workspace_id", "environment", "summary", "references", "resource_identity_fingerprint", "persisted"}
    if set(document) != (expected_active if phase == "active" else expected_terminal if phase == "terminal" else set()):
        raise EngineeringRecordError("invalid record fields")
    environment = _environment(document["environment"])
    if document["workspace_id"] != environment.workspace_id or document["schema_version"] != 1 or document["persisted"] is not True:
        raise EngineeringRecordError("invalid record invariant")
    try:
        return EngineeringRunSummaryV1(document["run_id"], document["operation_id"], EngineeringRunPhase(phase), None if document["status"] is None else EngineeringTerminalStatus(document["status"]), tuple(document["reason_codes"]), document["started_at_utc"], document["finished_at_utc"], document["duration_ms"], document["workspace_id"], True)
    except (TypeError, ValueError) as exc:
        raise EngineeringRecordError("invalid run record") from exc


def active_document(run_id: str, admission: Any, context: EngineeringExecutionContext, started: str) -> dict[str, Any]:
    fingerprint = hashlib.sha256(admission.resource_identity.encode("utf-8")).hexdigest() if admission.resource_identity else None
    return {"schema_version": 1, "run_id": run_id, "operation_id": admission.descriptor.operation_id, "phase": "active", "status": None, "reason_codes": [], "started_at_utc": started, "finished_at_utc": None, "duration_ms": None, "workspace_id": admission.environment.workspace_id, "environment": admission.environment.to_dict(), "owner_pid": os.getpid(), "owner_process_start_id": current_process_start_id(), "resource_identity_fingerprint": fingerprint, "persisted": True}


def transition_document(active: dict[str, Any], raw: bytes, created: str, mode: EngineeringTransitionMode) -> dict[str, Any]:
    return {"schema_version": 1, "run_id": active["run_id"], "operation_id": active["operation_id"], "started_at_utc": active["started_at_utc"], "workspace_id": active["workspace_id"], "environment": active["environment"], "resource_identity_fingerprint": active["resource_identity_fingerprint"], "active_sha256": hashlib.sha256(raw).hexdigest(), "created_at_utc": created, "mode": mode.value}


def validate_transition(document: dict[str, Any]) -> str:
    expected = {"schema_version", "run_id", "operation_id", "started_at_utc", "workspace_id", "environment", "resource_identity_fingerprint", "active_sha256", "created_at_utc", "mode"}
    if set(document) != expected or document.get("schema_version") != 1:
        raise EngineeringRecordError("invalid transition fields")
    try:
        run_id = validate_run_id(document["run_id"])
        validate_operation_id(document["operation_id"])
        parse_canonical_utc(document["started_at_utc"])
        parse_canonical_utc(document["created_at_utc"])
        environment = _environment(document["environment"])
        if document["workspace_id"] != environment.workspace_id:
            raise ValueError
        if document["workspace_id"] is not None:
            validate_workspace_id(document["workspace_id"])
        validate_sha256(document["resource_identity_fingerprint"], nullable=True)
        validate_sha256(document["active_sha256"])
        EngineeringTransitionMode(document["mode"])
    except (TypeError, ValueError) as exc:
        raise EngineeringRecordError("invalid transition") from exc
    return run_id


def terminal_document(active: dict[str, Any], intent: Any, finished: str, duration: int | None) -> dict[str, Any]:
    return {"schema_version": 1, "run_id": active["run_id"], "operation_id": active["operation_id"], "phase": "terminal", "status": intent.status.value, "reason_codes": list(order_reason_codes(list(intent.reason_codes))), "started_at_utc": active["started_at_utc"], "finished_at_utc": finished, "duration_ms": duration, "workspace_id": active["workspace_id"], "environment": active["environment"], "summary": dict(intent.summary), "references": [item.to_dict() for item in intent.references], "resource_identity_fingerprint": active["resource_identity_fingerprint"], "persisted": True}


def result_from_terminal(document: dict[str, Any]) -> EngineeringRunResultV1:
    summary = _summary_from_record(document)
    environment = _environment(document["environment"])
    references = tuple(EngineeringReferenceV1(**item) for item in document["references"])
    return EngineeringRunResultV1(**summary.__dict__, summary=document["summary"], references=references, environment=environment)


def history_projection(paths: Any, records: list[tuple[dict[str, Any], bytes, os.stat_result]], prove: Callable[[Any, str], None]) -> tuple[EngineeringRunSummaryV1, ...]:
    summaries = []
    for document, _, _ in records:
        summary = _summary_from_record(document)
        if summary.status is EngineeringTerminalStatus.SUCCEEDED:
            prove(paths, summary.run_id)
        summaries.append(summary)
    summaries.sort(key=lambda item: (-parse_canonical_utc(item.started_at_utc).timestamp(), item.run_id))
    return tuple(summaries)


def result_snapshot(paths: Any, document: dict[str, Any], raw: bytes, owner: Callable[[dict[str, Any]], Any], marker_needs_recovery: Callable[[Any, dict[str, Any], bytes], bool], prove: Callable[[Any, str], None]) -> EngineeringRunResultV1 | EngineeringErrorV1 | None:
    summary = _summary_from_record(document)
    if marker_needs_recovery(paths, document, raw):
        return None
    if summary.phase is EngineeringRunPhase.ACTIVE:
        state = owner(document)
        if state.value == 'dead':
            return None
        code = 'ENGINEERING_RUN_ACTIVE' if state.value == 'alive' else 'ENGINEERING_OWNER_LIVENESS_INDETERMINATE'
        return EngineeringErrorV1(EngineeringQueryStatus.BLOCKED, code, summary.run_id, summary)
    if summary.status is EngineeringTerminalStatus.SUCCEEDED:
        prove(paths, summary.run_id)
    return result_from_terminal(document)


def matching_marker_needs_recovery(paths: Any, run: dict[str, Any], raw: bytes, owner: Callable[[dict[str, Any]], Any], identity: Callable[[dict[str, Any], dict[str, Any]], bool], transition_limit: int) -> bool:
    marker_path = paths.engineering_transition_file(run['run_id'])
    if not marker_path.exists():
        return False
    marker, _, _ = _bounded_document(marker_path, transition_limit)
    validate_transition(marker)
    if not identity(marker, run):
        raise EngineeringRecordError('transition identity mismatch')
    mode = EngineeringTransitionMode(marker['mode'])
    summary = _summary_from_record(run)
    if mode is EngineeringTransitionMode.TERMINAL_PENDING:
        return True
    if summary.phase is EngineeringRunPhase.ACTIVE:
        if marker['active_sha256'] != hashlib.sha256(raw).hexdigest():
            raise EngineeringRecordError('active identity mismatch')
        return bool(owner(run).value == 'dead')
    return not (summary.status is EngineeringTerminalStatus.UNVERIFIED and 'ENGINEERING_BACKEND_STATE_INDETERMINATE' in summary.reason_codes)


def normalize_summary(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("summary root must be an object")
    count = [0]
    normalized = _normalize(value, depth=0, count=count)
    if not isinstance(normalized, dict) or len(canonical_json_bytes(normalized)) > MAX_ENGINEERING_SUMMARY_BYTES:
        raise ValueError("summary too large")
    return normalized


def _normalize(value: object, *, depth: int, count: list[int]) -> Any:
    if depth > MAX_ENGINEERING_SUMMARY_DEPTH:
        raise ValueError("summary too deep")
    scalar = _normalize_scalar(value)
    if scalar is not _COMPOSITE:
        return scalar
    if isinstance(value, Mapping):
        return _normalize_mapping(value, depth, count)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return _normalize_sequence(value, depth, count)
    raise ValueError("summary value is not JSON-native")


_COMPOSITE = object()


def _normalize_scalar(value: object) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite summary number")
        return value
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_ENGINEERING_SUMMARY_STRING_BYTES:
            raise ValueError("summary string too large")
        return value
    return _COMPOSITE


def _normalize_mapping(value: Mapping[object, object], depth: int, count: list[int]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        count[0] += 1
        if not isinstance(key, str) or len(key.encode("utf-8")) > MAX_ENGINEERING_SUMMARY_KEY_BYTES:
            raise ValueError("invalid summary object")
        _check_item_count(count)
        result[key] = _normalize(item, depth=depth + 1, count=count)
    return result


def _normalize_sequence(value: Sequence[object], depth: int, count: list[int]) -> list[Any]:
    result: list[Any] = []
    for item in value:
        count[0] += 1
        _check_item_count(count)
        result.append(_normalize(item, depth=depth + 1, count=count))
    return result


def _check_item_count(count: list[int]) -> None:
    if count[0] > MAX_ENGINEERING_SUMMARY_ITEMS:
        raise ValueError("too many summary items")


def validate_references(values: object) -> tuple[EngineeringReferenceV1, ...]:
    if (
        not isinstance(values, tuple)
        or len(values) > MAX_ENGINEERING_REFERENCES
        or not all(isinstance(item, EngineeringReferenceV1) for item in values)
    ):
        raise ValueError("invalid references")
    return values
