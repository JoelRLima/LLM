"""Private lock-held durable transactions for Engineering runs."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Any, Callable

from agent.engineering.contracts import (
    EngineeringExecutionContext,
    EngineeringRunPhase,
    EngineeringRunSummaryV1,
    EngineeringStoreError,
    EngineeringTerminalStatus,
    EngineeringTransitionMode,
    canonical_utc,
    order_reason_codes,
)
from agent.engineering.policy import (
    MAX_ENGINEERING_RUN_ID_ATTEMPTS,
    MAX_ENGINEERING_RUN_RECORD_BYTES,
    MAX_ENGINEERING_TRANSITION_RECORD_BYTES,
    EngineeringCapacityPolicy,
    EngineeringPreflight,
    same_identity,
)
from agent.engineering.registry import EngineeringTerminalIntent
from agent.engineering.summary import (
    EngineeringRecordError,
    _bounded_document,
    _summary_from_record,
    active_document,
    canonical_document_text,
    terminal_document,
    transition_document,
    validate_transition,
)
from agent.memory.json_persistence import AtomicWriteError, write_text_atomic
from agent.runtime.correlation import new_runtime_id
from agent.runtime.filesystem_primitives import inspect_final_path
from agent.runtime.instance_lock import InstanceLock, InstanceLockError
from agent.runtime.lock_filesystem import sync_parent_directory, unlink_if_observed
from agent.runtime.process_identity import OwnerLiveness

_RUN_FILE = re.compile(r"^(engr-[0-9a-f]{32})\.json$")
_TRANSITION_FILE = re.compile(r"^(engr-[0-9a-f]{32})\.pending\.json$")


def prove_success(paths: Any, run_id: str) -> None:
    marker = paths.engineering_transition_file(run_id)
    if inspect_final_path(marker).exists:
        raise OSError("transition marker still present")
    sync_parent_directory(marker)
    if inspect_final_path(marker).exists:
        raise OSError("transition marker appeared")


def remove_marker(path: Path) -> None:
    _, raw, observed = _bounded_document(path, MAX_ENGINEERING_TRANSITION_RECORD_BYTES)
    if not unlink_if_observed(path, observed, raw):
        raise OSError("marker identity changed")
    sync_parent_directory(path)


def write_document(path: Path, document: dict[str, Any], limit: int) -> None:
    text = canonical_document_text(document)
    if len(text.encode("utf-8")) > limit:
        raise EngineeringStoreError("ENGINEERING_RESULT_PERSIST_FAILED")
    try:
        write_text_atomic(path, text, create_parent=False)
    except AtomicWriteError as exc:
        if inspect_final_path(path).exists:
            raise EngineeringStoreError(
                "ENGINEERING_DURABLE_STATE_INDETERMINATE",
                candidate_run_id=document.get("run_id"),
            ) from exc
        raise EngineeringStoreError("ENGINEERING_RESULT_PERSIST_FAILED") from exc


def enforce_capacity(paths: Any, owner_liveness: OwnerLiveness) -> None:
    """Execute policy-selected eviction under the caller's store lock."""
    policy = EngineeringCapacityPolicy()
    _capacity_snapshot(paths)
    sync_parent_directory(paths.engineering_run_file("engr-" + "0" * 32))
    sync_parent_directory(paths.engineering_transition_file("engr-" + "0" * 32))
    run_count, transition_count, total, candidates, protected = _capacity_snapshot(paths)
    ordered = sorted((item[0], item[1]) for item in candidates)
    while policy._over_capacity(run_count, transition_count, total):
        candidate = policy.select_evictable(ordered, protected)
        ordered.remove(candidate)
        index = next(index for index, item in enumerate(candidates) if item[:2] == candidate)
        _, run_id, path, raw, observed = candidates.pop(index)
        with InstanceLock.create(paths.engineering_run_lock_file(run_id), owner_liveness=owner_liveness, create_parent=False):
            if not unlink_if_observed(path, observed, raw):
                raise EngineeringStoreError("ENGINEERING_DURABLE_STATE_INDETERMINATE")
            try:
                sync_parent_directory(path)
            except OSError as exc:
                raise EngineeringStoreError("ENGINEERING_DURABLE_STATE_INDETERMINATE") from exc
        run_count -= 1
        total -= len(raw)


def _capacity_snapshot(paths: Any) -> tuple[int, int, int, list[tuple[str, str, Path, bytes, os.stat_result]], set[str]]:
    run_count = transition_count = total = 0
    candidates: list[tuple[str, str, Path, bytes, os.stat_result]] = []
    protected: set[str] = set()
    for directory, pattern, kind in ((paths.engineering_runs_dir, _RUN_FILE, "run"), (paths.engineering_transitions_dir, _TRANSITION_FILE, "transition")):
        for entry in directory.iterdir():
            inspection = inspect_final_path(entry)
            metadata = inspection.metadata
            if inspection.is_link_like or metadata is None or not stat.S_ISREG(metadata.st_mode) or not pattern.fullmatch(entry.name):
                raise EngineeringStoreError("ENGINEERING_STORE_STATE_UNSAFE")
            total += metadata.st_size
            if kind == "run":
                run_count += 1
                try:
                    document, raw, observed = _bounded_document(entry, MAX_ENGINEERING_RUN_RECORD_BYTES)
                    summary = _summary_from_record(document)
                except EngineeringRecordError as exc:
                    raise EngineeringStoreError("ENGINEERING_RUN_RECORD_CORRUPT") from exc
                if summary.phase is EngineeringRunPhase.TERMINAL:
                    candidates.append((summary.finished_at_utc or "", summary.run_id, entry, raw, observed))
            else:
                transition_count += 1
                try:
                    marker, _, _ = _bounded_document(entry, MAX_ENGINEERING_TRANSITION_RECORD_BYTES)
                    protected.add(validate_transition(marker))
                except EngineeringRecordError as exc:
                    raise EngineeringStoreError("ENGINEERING_STORE_STATE_UNSAFE") from exc
    return run_count, transition_count, total, candidates, protected


def publish_active_record(
    paths: Any,
    admission: EngineeringPreflight,
    context: EngineeringExecutionContext,
    owner_liveness: OwnerLiveness,
    check_safe_point: Callable[[EngineeringExecutionContext], None],
) -> dict[str, Any]:
    """Allocate and create one ACTIVE record beneath the caller's store lock."""
    run_id = allocate_run_id(paths)
    check_safe_point(context)
    with InstanceLock.create(paths.engineering_run_lock_file(run_id), owner_liveness=owner_liveness, create_parent=False):
        check_safe_point(context)
        document = active_document(run_id, admission, context, canonical_utc(context.utc_now()))
        write_document(paths.engineering_run_file(run_id), document, MAX_ENGINEERING_RUN_RECORD_BYTES)
    return document


def allocate_run_id(paths: Any) -> str:
    for _ in range(MAX_ENGINEERING_RUN_ID_ATTEMPTS):
        run_id = "engr-" + new_runtime_id()
        if not paths.engineering_run_file(run_id).exists():
            return run_id
    raise EngineeringStoreError("ENGINEERING_INTERNAL_ERROR")


def scoped_run_exists(
    paths: Any,
    run_id: str,
    workspace_id: str,
    belongs_to_workspace: Callable[[dict[str, Any], str], bool],
) -> bool:
    """Reread workspace membership beneath the caller's store lock."""
    return scoped_run_document(paths, run_id, workspace_id, belongs_to_workspace) is not None


def scoped_run_document(
    paths: Any,
    run_id: str,
    workspace_id: str,
    belongs_to_workspace: Callable[[dict[str, Any], str], bool],
) -> tuple[dict[str, Any], bytes] | None:
    """Fail closed until canonical run identity and workspace are proven."""
    path = paths.engineering_run_file(run_id)
    try:
        document, raw, _ = _bounded_document(path, MAX_ENGINEERING_RUN_RECORD_BYTES)
        summary = _summary_from_record(document)
    except (EngineeringRecordError, OSError):
        return None
    if summary.run_id != run_id or not belongs_to_workspace(document, workspace_id):
        return None
    return document, raw


def persist_managed_guard(
    active: EngineeringRunSummaryV1,
    admission: EngineeringPreflight,
    context: EngineeringExecutionContext,
    owner_liveness: OwnerLiveness,
    check_safe_point: Callable[[EngineeringExecutionContext], None],
) -> None:
    """Reread ACTIVE and durably bind its managed-execution marker."""
    paths = context.app_paths
    try:
        check_safe_point(context)
        with InstanceLock.create(paths.engineering_store_lock_file, owner_liveness=owner_liveness, create_parent=False):
            check_safe_point(context)
            check_safe_point(context)
            with InstanceLock.create(paths.engineering_run_lock_file(active.run_id), owner_liveness=owner_liveness, create_parent=False):
                check_safe_point(context)
                document, raw, _ = _bounded_document(paths.engineering_run_file(active.run_id), MAX_ENGINEERING_RUN_RECORD_BYTES)
                _summary_from_record(document)
                marker = transition_document(document, raw, canonical_utc(context.utc_now()), EngineeringTransitionMode.MANAGED_EXECUTION_GUARD)
                write_document(paths.engineering_transition_file(active.run_id), marker, MAX_ENGINEERING_TRANSITION_RECORD_BYTES)
    except (InstanceLockError, EngineeringRecordError) as exc:
        raise EngineeringStoreError("ENGINEERING_DURABLE_STATE_INDETERMINATE") from exc


def commit_terminal_record(
    paths: Any,
    active: EngineeringRunSummaryV1,
    admission: EngineeringPreflight,
    intent: EngineeringTerminalIntent,
    context: EngineeringExecutionContext,
    owner_liveness: OwnerLiveness,
    started_lookup: Callable[[str], float | None],
) -> dict[str, Any]:
    """Commit terminal state and marker disposition under store/run locks."""
    intent = _intent_after_settlement_safe_point(intent, context)
    with InstanceLock.create(paths.engineering_store_lock_file, owner_liveness=owner_liveness, create_parent=False):
        intent = _intent_after_settlement_safe_point(intent, context)
        intent = _intent_after_settlement_safe_point(intent, context)
        with InstanceLock.create(paths.engineering_run_lock_file(active.run_id), owner_liveness=owner_liveness, create_parent=False):
            intent = _intent_after_settlement_safe_point(intent, context)
            current, raw, _ = _bounded_document(paths.engineering_run_file(active.run_id), MAX_ENGINEERING_RUN_RECORD_BYTES)
            if _summary_from_record(current).phase is not EngineeringRunPhase.ACTIVE:
                raise EngineeringRecordError("ACTIVE changed")
            marker_path = paths.engineering_transition_file(active.run_id)
            marker = _terminal_marker(marker_path, current, raw, admission, context)
            if marker.get("active_sha256") != hashlib.sha256(raw).hexdigest() or not same_identity(marker, current):
                raise EngineeringRecordError("transition mismatch")
            again, again_raw, _ = _bounded_document(paths.engineering_run_file(active.run_id), MAX_ENGINEERING_RUN_RECORD_BYTES)
            if again_raw != raw or not same_identity(marker, again):
                raise EngineeringRecordError("ACTIVE changed")
            intent = _intent_after_settlement_safe_point(intent, context)
            terminal = _build_terminal(active.run_id, current, intent, context, started_lookup)
            write_document(paths.engineering_run_file(active.run_id), terminal, MAX_ENGINEERING_RUN_RECORD_BYTES)
            retain = admission.descriptor.effects.managed_external_process and "ENGINEERING_BACKEND_STATE_INDETERMINATE" in intent.reason_codes
            if not retain:
                remove_marker(marker_path)
            return terminal


def _intent_after_settlement_safe_point(intent: EngineeringTerminalIntent, context: EngineeringExecutionContext) -> EngineeringTerminalIntent:
    cancelled = bool(context.cancellation_token and context.cancellation_token.is_set())
    deadline = context.deadline_monotonic is not None and context.monotonic_now() >= context.deadline_monotonic
    if not cancelled and not deadline:
        return intent
    reasons = list(intent.reason_codes)
    if cancelled:
        reasons.append("ENGINEERING_CANCELLED")
    if deadline:
        reasons.append("ENGINEERING_DEADLINE_EXCEEDED")
    status = EngineeringTerminalStatus.UNVERIFIED if intent.status is EngineeringTerminalStatus.UNVERIFIED else EngineeringTerminalStatus.FAILED
    return EngineeringTerminalIntent(status, order_reason_codes(reasons), {}, ())


def _terminal_marker(path: Path, current: dict[str, Any], raw: bytes, admission: EngineeringPreflight, context: EngineeringExecutionContext) -> dict[str, Any]:
    if admission.descriptor.effects.managed_external_process:
        marker, _, _ = _bounded_document(path, MAX_ENGINEERING_TRANSITION_RECORD_BYTES)
        validate_transition(marker)
        if marker.get("mode") != EngineeringTransitionMode.MANAGED_EXECUTION_GUARD.value:
            raise EngineeringRecordError("invalid guard")
        return marker
    marker = transition_document(current, raw, canonical_utc(context.utc_now()), EngineeringTransitionMode.TERMINAL_PENDING)
    write_document(path, marker, MAX_ENGINEERING_TRANSITION_RECORD_BYTES)
    return marker


def _build_terminal(run_id: str, current: dict[str, Any], intent: EngineeringTerminalIntent, context: EngineeringExecutionContext, started_lookup: Callable[[str], float | None]) -> dict[str, Any]:
    started = started_lookup(run_id)
    duration = None if started is None else max(0, int((context.monotonic_now() - started) * 1000))
    finished = canonical_utc(context.utc_now())
    terminal = terminal_document(current, intent, finished, duration)
    if len(canonical_document_text(terminal).encode("utf-8")) <= MAX_ENGINEERING_RUN_RECORD_BYTES:
        return terminal
    fallback = EngineeringTerminalIntent(EngineeringTerminalStatus.UNVERIFIED, ("ENGINEERING_RESULT_PERSIST_FAILED",), {}, ())
    return terminal_document(current, fallback, finished, duration)


def write_recovery_tombstone(
    paths: Any,
    marker_path: Path,
    run: dict[str, Any],
    context: EngineeringExecutionContext,
    owner_liveness: OwnerLiveness,
    observe_safe_point: Callable[[EngineeringExecutionContext], None],
) -> None:
    """Persist an indeterminate terminal record and remove its marker."""
    run_id = run["run_id"]
    observe_safe_point(context)
    with InstanceLock.create(paths.engineering_run_lock_file(run_id), owner_liveness=owner_liveness, create_parent=False):
        observe_safe_point(context)
        terminal = terminal_document(run, EngineeringTerminalIntent(EngineeringTerminalStatus.UNVERIFIED, ("ENGINEERING_RESULT_PERSIST_FAILED", "ENGINEERING_DURABLE_STATE_INDETERMINATE"), {}, ()), canonical_utc(context.utc_now()), None)
        write_document(paths.engineering_run_file(run_id), terminal, MAX_ENGINEERING_RUN_RECORD_BYTES)
        observe_safe_point(context)
        remove_marker(marker_path)
        observe_safe_point(context)


def terminalize_dead_active(
    paths: Any,
    document: dict[str, Any],
    raw: bytes,
    context: EngineeringExecutionContext,
    owner_liveness: OwnerLiveness,
    observe_safe_point: Callable[[EngineeringExecutionContext], None],
) -> EngineeringRunSummaryV1:
    """Complete dead-owner terminalization under the run lock."""
    run_id = document["run_id"]
    with InstanceLock.create(paths.engineering_run_lock_file(run_id), owner_liveness=owner_liveness, create_parent=False):
        observe_safe_point(context)
        marker_path = paths.engineering_transition_file(run_id)
        managed = marker_path.exists()
        if managed:
            marker, _, _ = _bounded_document(marker_path, MAX_ENGINEERING_TRANSITION_RECORD_BYTES)
            try:
                validate_transition(marker)
            except EngineeringRecordError as exc:
                raise EngineeringStoreError("ENGINEERING_STORE_STATE_UNSAFE") from exc
            if marker.get("mode") != EngineeringTransitionMode.MANAGED_EXECUTION_GUARD.value or not same_identity(marker, document) or marker.get("active_sha256") != hashlib.sha256(raw).hexdigest():
                raise EngineeringRecordError("invalid managed guard")
            reasons: tuple[str, ...] = ("ENGINEERING_BACKEND_STATE_INDETERMINATE", "ENGINEERING_OWNER_DEAD")
        else:
            marker = transition_document(document, raw, canonical_utc(context.utc_now()), EngineeringTransitionMode.TERMINAL_PENDING)
            write_document(marker_path, marker, MAX_ENGINEERING_TRANSITION_RECORD_BYTES)
            reasons = ("ENGINEERING_OWNER_DEAD",)
        terminal = terminal_document(document, EngineeringTerminalIntent(EngineeringTerminalStatus.UNVERIFIED, reasons, {}, ()), canonical_utc(context.utc_now()), None)
        write_document(paths.engineering_run_file(run_id), terminal, MAX_ENGINEERING_RUN_RECORD_BYTES)
        observe_safe_point(context)
        if not managed:
            remove_marker(marker_path)
        observe_safe_point(context)
        return _summary_from_record(terminal)
