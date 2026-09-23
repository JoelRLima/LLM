"""Engineering recovery decisions over store-owned durable transactions."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

from agent.engineering.contracts import (
    EngineeringErrorV1,
    EngineeringExecutionContext,
    EngineeringQueryStatus,
    EngineeringRunPhase,
    EngineeringRunSummaryV1,
    EngineeringStoreError,
    EngineeringTerminalStatus,
    EngineeringTransitionMode,
)
from agent.engineering.policy import EngineeringPreflight, owner_status, resource_fingerprint
from agent.engineering.summary import _summary_from_record, active_from_transition, result_from_terminal
from agent.runtime.process_identity import OwnerLiveness, OwnerStatus


class RecoveryStore(Protocol):
    _owner_liveness: OwnerLiveness

    def recover_or_project_active(
        self,
        paths: Any,
        document: dict[str, Any],
        raw: bytes,
        context: EngineeringExecutionContext,
    ) -> EngineeringRunSummaryV1: ...

    def _write_recovery_tombstone(
        self,
        paths: Any,
        marker_path: Any,
        run: dict[str, Any],
        context: EngineeringExecutionContext,
    ) -> None: ...

    def _read_transition_document(
        self, marker_path: Any
    ) -> tuple[dict[str, Any], bytes, Any]: ...

    def _transition_for_run(
        self, paths: Any, run_id: str
    ) -> tuple[Any, dict[str, Any], dict[str, Any] | None, bytes, EngineeringRunSummaryV1 | None] | None: ...

    def _observe_reconciliation_safe_point(
        self, context: EngineeringExecutionContext
    ) -> None: ...

    def _transition_records(
        self, paths: Any
    ) -> Iterator[tuple[Any, dict[str, Any], dict[str, Any] | None, bytes, EngineeringRunSummaryV1 | None]]: ...

    def _unmarked_run_record(
        self, paths: Any, run_path: Any
    ) -> tuple[dict[str, Any], bytes, EngineeringRunSummaryV1, bool]: ...

    def _transition_paths(self, paths: Any) -> list[Any]: ...

    def _run_paths(self, paths: Any) -> list[Any]: ...

    def _transition_record_at(
        self, paths: Any, marker_path: Any
    ) -> tuple[dict[str, Any], dict[str, Any] | None, bytes, EngineeringRunSummaryV1 | None]: ...

    def _scoped_run_exists(self, paths: Any, run_id: str, workspace_id: str) -> bool: ...

    def _read_run_records(self, paths: Any) -> list[tuple[dict[str, Any], bytes, Any]]: ...

    def _read_run_document(self, paths: Any, run_id: str) -> tuple[dict[str, Any], bytes, Any]: ...

    def _project_recovered_history(
        self, paths: Any, records: list[tuple[dict[str, Any], bytes, Any]]
    ) -> tuple[EngineeringRunSummaryV1, ...]: ...

    def _prove_success(self, paths: Any, run_id: str) -> None: ...

    def _validate_active_marker(self, marker: dict[str, Any], raw: bytes) -> None: ...

    def _terminalize_dead_active(
        self, paths: Any, document: dict[str, Any], raw: bytes, context: EngineeringExecutionContext
    ) -> EngineeringRunSummaryV1: ...


def reconcile_active(
    store: RecoveryStore,
    paths: Any,
    run: dict[str, Any],
    raw: bytes,
    context: EngineeringExecutionContext,
    marker: dict[str, Any],
) -> None:
    """Validate the active marker and dispatch dead-owner mutation to store."""
    store._validate_active_marker(marker, raw)
    if owner_status(run, store._owner_liveness) is OwnerStatus.DEAD:
        store.recover_or_project_active(paths, run, raw, context)


def reconcile_managed_guard(
    store: RecoveryStore,
    paths: Any,
    marker_path: Any,
    marker: dict[str, Any],
    run: dict[str, Any],
    raw: bytes,
    summary: EngineeringRunSummaryV1,
    admission: EngineeringPreflight,
    context: EngineeringExecutionContext,
) -> None:
    """Classify a managed guard; let store perform any durable mutation."""
    if summary.phase is EngineeringRunPhase.ACTIVE:
        store._validate_active_marker(marker, raw)
        if owner_status(run, store._owner_liveness) is OwnerStatus.DEAD:
            store.recover_or_project_active(paths, run, raw, context)
        return
    quarantined = (
        summary.status is EngineeringTerminalStatus.UNVERIFIED
        and "ENGINEERING_BACKEND_STATE_INDETERMINATE" in summary.reason_codes
    )
    matches = (
        marker["resource_identity_fingerprint"]
        == resource_fingerprint(admission.resource_identity)
        if marker["resource_identity_fingerprint"] is not None
        else marker["operation_id"] == admission.descriptor.operation_id
    )
    if quarantined:
        if matches:
            raise EngineeringStoreError("ENGINEERING_BACKEND_STATE_INDETERMINATE")
        return
    store._write_recovery_tombstone(paths, marker_path, run, context)


def reconcile_terminal_pending(
    store: RecoveryStore,
    paths: Any,
    marker_path: Any,
    run: dict[str, Any] | None,
    context: EngineeringExecutionContext,
) -> None:
    """Reconstruct a missing run from its marker, then request a tombstone."""
    if run is None:
        marker, _, _ = store._read_transition_document(marker_path)
        run = active_from_transition(marker)
    store._write_recovery_tombstone(paths, marker_path, run, context)


def reconcile_query_marker(
    store: RecoveryStore,
    paths: Any,
    run_id: str,
    context: EngineeringExecutionContext,
) -> None:
    """Dispatch one store-loaded query marker after the scoped lookup."""
    record = store._transition_for_run(paths, run_id)
    if record is None:
        return
    marker_path, marker, run, raw, summary = record
    store._observe_reconciliation_safe_point(context)
    _dispatch_query_transition(store, paths, marker_path, marker, run, raw, summary, context)
    store._observe_reconciliation_safe_point(context)


def reconcile_query_markers(
    store: RecoveryStore,
    paths: Any,
    context: EngineeringExecutionContext,
) -> None:
    """Dispatch all transitions while store owns their durable reads."""
    for marker_path, marker, run, raw, summary in store._transition_records(paths):
        store._observe_reconciliation_safe_point(context)
        _dispatch_query_transition(store, paths, marker_path, marker, run, raw, summary, context)
        store._observe_reconciliation_safe_point(context)


def reconcile_unmarked_run(
    store: RecoveryStore,
    paths: Any,
    run_path: Any,
    context: EngineeringExecutionContext,
) -> None:
    """Decide dead-owner recovery after a store-owned validated read."""
    store._observe_reconciliation_safe_point(context)
    run, raw, summary, unmarked = store._unmarked_run_record(paths, run_path)
    if summary.phase is EngineeringRunPhase.ACTIVE and unmarked:
        if owner_status(run, store._owner_liveness) is OwnerStatus.DEAD:
            store.recover_or_project_active(paths, run, raw, context)
    store._observe_reconciliation_safe_point(context)


def reconcile_existing(
    store: RecoveryStore,
    paths: Any,
    admission: EngineeringPreflight,
    context: EngineeringExecutionContext,
) -> None:
    """Dispatch recovery beneath the store lock held by active publication."""
    store._observe_reconciliation_safe_point(context)
    for marker_path in store._transition_paths(paths):
        store._observe_reconciliation_safe_point(context)
        marker, run, raw, summary = store._transition_record_at(paths, marker_path)
        if EngineeringTransitionMode(marker["mode"]) is EngineeringTransitionMode.MANAGED_EXECUTION_GUARD:
            if run is None or summary is None:
                raise EngineeringStoreError("ENGINEERING_STORE_STATE_UNSAFE")
            reconcile_managed_guard(store, paths, marker_path, marker, run, raw, summary, admission, context)
        else:
            reconcile_terminal_pending(store, paths, marker_path, run, context)
        store._observe_reconciliation_safe_point(context)
    for run_path in store._run_paths(paths):
        store._observe_reconciliation_safe_point(context)
        reconcile_unmarked_run(store, paths, run_path, context)
        store._observe_reconciliation_safe_point(context)


def recover_query_locked(
    store: RecoveryStore,
    paths: Any,
    context: EngineeringExecutionContext,
    run_id: str | None,
    scoped_workspace_id: str | None,
) -> Any:
    """Choose recovery and projection under the store-held transaction lock."""
    if scoped_workspace_id is not None and run_id is not None:
        if not store._scoped_run_exists(paths, run_id, scoped_workspace_id):
            return EngineeringErrorV1(EngineeringQueryStatus.BLOCKED, "ENGINEERING_RUN_NOT_FOUND")
        reconcile_query_marker(store, paths, run_id, context)
    else:
        reconcile_query_markers(store, paths, context)
    if run_id is None:
        recovered = []
        for document, raw, _ in store._read_run_records(paths):
            if _summary_from_record(document).phase is EngineeringRunPhase.ACTIVE:
                if owner_status(document, store._owner_liveness) is OwnerStatus.DEAD:
                    store.recover_or_project_active(paths, document, raw, context)
            recovered.append(store._read_run_document(paths, document["run_id"]))
        return store._project_recovered_history(paths, recovered)
    document, raw, _ = store._read_run_document(paths, run_id)
    summary = _summary_from_record(document)
    if summary.phase is EngineeringRunPhase.ACTIVE and owner_status(document, store._owner_liveness) is OwnerStatus.DEAD:
        store.recover_or_project_active(paths, document, raw, context)
        document, _, _ = store._read_run_document(paths, run_id)
        summary = _summary_from_record(document)
    if summary.phase is EngineeringRunPhase.ACTIVE:
        code = "ENGINEERING_RUN_ACTIVE" if owner_status(document, store._owner_liveness) is OwnerStatus.ALIVE else "ENGINEERING_OWNER_LIVENESS_INDETERMINATE"
        return EngineeringErrorV1(EngineeringQueryStatus.BLOCKED, code, run_id, summary)
    if summary.status is EngineeringTerminalStatus.SUCCEEDED:
        store._prove_success(paths, run_id)
    return result_from_terminal(document)


def recover_or_project_active(
    store: RecoveryStore,
    paths: Any,
    document: dict[str, Any],
    raw: bytes,
    context: EngineeringExecutionContext,
) -> EngineeringRunSummaryV1:
    """Choose dead-owner terminalization; store owns its locked transaction."""
    store._observe_reconciliation_safe_point(context)
    if owner_status(document, store._owner_liveness) is not OwnerStatus.DEAD:
        return _summary_from_record(document)
    return store._terminalize_dead_active(paths, document, raw, context)


def _dispatch_query_transition(
    store: RecoveryStore,
    paths: Any,
    marker_path: Any,
    marker: dict[str, Any],
    run: dict[str, Any] | None,
    raw: bytes,
    summary: EngineeringRunSummaryV1 | None,
    context: EngineeringExecutionContext,
) -> None:
    mode = EngineeringTransitionMode(marker["mode"])
    if mode is EngineeringTransitionMode.TERMINAL_PENDING:
        reconcile_terminal_pending(store, paths, marker_path, run, context)
    elif run is None or summary is None:
        raise EngineeringStoreError("ENGINEERING_STORE_STATE_UNSAFE")
    elif summary.phase is EngineeringRunPhase.ACTIVE:
        reconcile_active(store, paths, run, raw, context, marker)
    elif not (
        summary.status is EngineeringTerminalStatus.UNVERIFIED
        and "ENGINEERING_BACKEND_STATE_INDETERMINATE" in summary.reason_codes
    ):
        store._write_recovery_tombstone(paths, marker_path, run, context)
