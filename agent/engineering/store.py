"""Bounded, canonical and crash-conservative Engineering run store."""

from __future__ import annotations

import hashlib
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, cast

from agent.engineering.contracts import (
    EngineeringErrorV1,
    EngineeringExecutionContext,
    EngineeringQueryStatus,
    EngineeringRunPhase,
    EngineeringRunResultV1,
    EngineeringRunSummaryV1,
    EngineeringStoreError,
    validate_run_id,
)
from agent.engineering.policy import (
    MAX_ENGINEERING_RUN_RECORD_BYTES,
    MAX_ENGINEERING_TRANSITION_RECORD_BYTES,
    EngineeringPreflight,
    owner_status,
    safe_point_error,
    same_identity,
)
from agent.engineering.recovery import reconcile_existing, recover_or_project_active, recover_query_locked
from agent.engineering.registry import EngineeringTerminalIntent
from agent.engineering.summary import (
    EngineeringRecordError,
    _bounded_document,
    _summary_from_record,
    canonical_document_text,
    history_projection,
    load_transition_record,
    matching_marker_needs_recovery,
    read_run_records,
    result_from_terminal,
    result_snapshot,
)
from agent.engineering.transactions import (
    commit_terminal_record,
    enforce_capacity,
    prove_success,
    publish_active_record,
    scoped_run_document,
    scoped_run_exists,
    terminalize_dead_active,
    write_recovery_tombstone,
)
from agent.engineering.transactions import (
    persist_managed_guard as persist_guard_transaction,
)
from agent.memory.json_persistence import AtomicWriteError
from agent.runtime.home_lifecycle import HomeLifecycleLease
from agent.runtime.instance_lock import InstanceLock, InstanceLockError
from agent.runtime.process_identity import OwnerLiveness, OwnerStatus, ProcessOwnerLiveness
from agent.runtime.storage_bootstrap import StorageBootstrap
from agent.runtime.storage_contracts import StorageMaintenanceError

_RUN_FILE = re.compile(r"^(engr-[0-9a-f]{32})\.json$")
_TRANSITION_FILE = re.compile(r"^(engr-[0-9a-f]{32})\.pending\.json$")

def _belongs_to_workspace(document: dict[str, Any], workspace_id: str) -> bool:
    environment = document.get("environment")
    return (
        document.get("workspace_id") == workspace_id
        and isinstance(environment, dict)
        and environment.get("workspace_id") == workspace_id
    )


__all__ = ["MAX_ENGINEERING_RUN_RECORD_BYTES", "EngineeringRecordError", "EngineeringRunStore", "_bounded_document", "canonical_document_text"]


class EngineeringRunStore:
    def __init__(self, owner_liveness: OwnerLiveness | None=None) -> None:
        self._owner_liveness = owner_liveness or ProcessOwnerLiveness()
        self._resource_locks: dict[str, InstanceLock] = {}
        self._started_monotonic: dict[str, float] = {}
        self._local_guard = threading.Lock()
    def begin(self, admission: EngineeringPreflight, context: EngineeringExecutionContext) -> EngineeringRunSummaryV1:
        paths = context.app_paths
        self._check_safe_point(context)
        try:
            paths.validate_engineering_directories()
        except OSError as exc:
            raise EngineeringStoreError('ENGINEERING_STORE_STATE_UNSAFE') from exc
        resource_lock = None
        if admission.resource_identity is not None:
            self._check_safe_point(context)
            resource_lock = InstanceLock.create(paths.engineering_resource_lock_file(admission.resource_identity), owner_liveness=self._owner_liveness, create_parent=False)
            try:
                resource_lock.acquire()
                self._check_safe_point(context)
            except InstanceLockError as exc:
                raise EngineeringStoreError('ENGINEERING_LOCK_BUSY') from exc
        try:
            document = self._publish_active(paths, admission, context)
            run_id = document['run_id']
            if resource_lock is not None:
                with self._local_guard:
                    self._resource_locks[run_id] = resource_lock
            with self._local_guard:
                self._started_monotonic[run_id] = context.monotonic_now()
            return _summary_from_record(document)
        except InstanceLockError as exc:
            _release_lock(resource_lock)
            raise EngineeringStoreError('ENGINEERING_LOCK_BUSY') from exc
        except EngineeringStoreError:
            _release_lock(resource_lock)
            raise
        except OSError as exc:
            _release_lock(resource_lock)
            raise EngineeringStoreError('ENGINEERING_DURABLE_STATE_INDETERMINATE') from exc
    def _publish_active(self, paths: Any, admission: EngineeringPreflight, context: EngineeringExecutionContext) -> dict[str, Any]:
        self._check_safe_point(context)
        with InstanceLock.create(paths.engineering_store_lock_file, owner_liveness=self._owner_liveness, create_parent=False):
            self._check_safe_point(context)
            reconcile_existing(self, paths, admission, context)
            enforce_capacity(paths, self._owner_liveness)
            document = publish_active_record(paths, admission, context, self._owner_liveness, self._check_safe_point)
        return document

    @staticmethod
    def _check_safe_point(context: EngineeringExecutionContext) -> None:
        if (observed := safe_point_error(context)) is not None:
            raise EngineeringStoreError(observed.code)

    @staticmethod
    def _observe_reconciliation_safe_point(context: EngineeringExecutionContext) -> None:
        safe_point_error(context)
    def release_local_ownership(self, active: EngineeringRunSummaryV1) -> None:
        with self._local_guard:
            resource = self._resource_locks.pop(active.run_id, None)
            self._started_monotonic.pop(active.run_id, None)
        if resource is not None:
            resource.release()

    def persist_managed_guard(self, active: EngineeringRunSummaryV1, admission: EngineeringPreflight, context: EngineeringExecutionContext) -> None:
        persist_guard_transaction(active, admission, context, self._owner_liveness, self._check_safe_point)

    def commit_terminal(self, active: EngineeringRunSummaryV1, admission: EngineeringPreflight, intent: EngineeringTerminalIntent, context: EngineeringExecutionContext) -> EngineeringRunResultV1:
        try:
            terminal = self._commit_terminal(context.app_paths, active, admission, intent, context)
            return result_from_terminal(terminal)
        except InstanceLockError as exc:
            raise EngineeringStoreError("ENGINEERING_DURABLE_STATE_INDETERMINATE") from exc
        except EngineeringRecordError as exc:
            raise EngineeringStoreError("ENGINEERING_STORE_STATE_UNSAFE") from exc
        except (AtomicWriteError, OSError) as exc:
            raise EngineeringStoreError("ENGINEERING_DURABLE_STATE_INDETERMINATE") from exc
        finally:
            self._observe_reconciliation_safe_point(context)
            self.release_local_ownership(active)
            self._observe_reconciliation_safe_point(context)

    def _commit_terminal(self, paths: Any, active: EngineeringRunSummaryV1, admission: EngineeringPreflight, intent: EngineeringTerminalIntent, context: EngineeringExecutionContext) -> dict[str, Any]:
        return commit_terminal_record(paths, active, admission, intent, context, self._owner_liveness, self._started_for_run)

    def _started_for_run(self, run_id: str) -> float | None:
        with self._local_guard:
            return self._started_monotonic.get(run_id)
    @staticmethod
    def _transition_paths(paths: Any) -> list[Path]:
        return sorted(paths.engineering_transitions_dir.iterdir(), key=lambda item: item.name)

    @staticmethod
    def _run_paths(paths: Any) -> list[Path]:
        return sorted(paths.engineering_runs_dir.iterdir(), key=lambda item: item.name)

    def _transition_record_at(self, paths: Any, marker_path: Path) -> tuple[dict[str, Any], dict[str, Any] | None, bytes, EngineeringRunSummaryV1 | None]:
        if _TRANSITION_FILE.fullmatch(marker_path.name) is None:
            raise EngineeringStoreError("ENGINEERING_STORE_STATE_UNSAFE")
        return self._load_transition(paths, marker_path)
    def _unmarked_run_record(self, paths: Any, run_path: Path) -> tuple[dict[str, Any], bytes, EngineeringRunSummaryV1, bool]:
        if _RUN_FILE.fullmatch(run_path.name) is None:
            raise EngineeringStoreError('ENGINEERING_STORE_STATE_UNSAFE')
        try:
            run, raw, _ = _bounded_document(run_path, MAX_ENGINEERING_RUN_RECORD_BYTES)
            summary = _summary_from_record(run)
        except EngineeringRecordError as exc:
            raise EngineeringStoreError('ENGINEERING_RUN_RECORD_CORRUPT') from exc
        unmarked = not paths.engineering_transition_file(summary.run_id).exists()
        return run, raw, summary, unmarked
    def _load_transition(self, paths: Any, marker_path: Path) -> tuple[dict[str, Any], dict[str, Any] | None, bytes, EngineeringRunSummaryV1 | None]:
        try:
            return load_transition_record(paths, marker_path, MAX_ENGINEERING_TRANSITION_RECORD_BYTES, MAX_ENGINEERING_RUN_RECORD_BYTES, same_identity)
        except EngineeringRecordError as exc:
            raise EngineeringStoreError('ENGINEERING_STORE_STATE_UNSAFE') from exc
    def _transition_for_run(self, paths: Any, run_id: str) -> tuple[Path, dict[str, Any], dict[str, Any] | None, bytes, EngineeringRunSummaryV1 | None] | None:
        marker_path = paths.engineering_transition_file(run_id)
        if not marker_path.exists():
            return None
        return (marker_path, *self._load_transition(paths, marker_path))
    def _read_transition_document(self, marker_path: Path) -> tuple[dict[str, Any], bytes, os.stat_result]:
        return _bounded_document(marker_path, MAX_ENGINEERING_TRANSITION_RECORD_BYTES)
    @staticmethod
    def _validate_active_marker(marker: dict[str, Any], raw: bytes) -> None:
        if marker["active_sha256"] != hashlib.sha256(raw).hexdigest():
            raise EngineeringStoreError("ENGINEERING_STORE_STATE_UNSAFE")
    def _write_recovery_tombstone(self, paths: Any, marker_path: Path, run: dict[str, Any], context: EngineeringExecutionContext) -> None:
        write_recovery_tombstone(paths, marker_path, run, context, self._owner_liveness, self._observe_reconciliation_safe_point)
    def publish_managed_guard(self, active: EngineeringRunSummaryV1, admission: EngineeringPreflight, context: EngineeringExecutionContext) -> None:
        self.persist_managed_guard(active, admission, context)
    def finish(self, active: EngineeringRunSummaryV1, admission: EngineeringPreflight, intent: EngineeringTerminalIntent, context: EngineeringExecutionContext) -> EngineeringRunResultV1:
        return self.commit_terminal(active, admission, intent, context)
    def history(self, context: EngineeringExecutionContext) -> tuple[EngineeringRunSummaryV1, ...] | EngineeringErrorV1:
        paths = context.app_paths
        if not paths.engineering_runs_dir.exists():
            return ()
        try:
            paths.validate_engineering_directories()
            needs_recovery = False
            records: list[tuple[dict[str, Any], bytes, os.stat_result]] = []
            with InstanceLock.create(paths.engineering_store_lock_file, owner_liveness=self._owner_liveness, create_parent=False):
                records = read_run_records(paths, _RUN_FILE, MAX_ENGINEERING_RUN_RECORD_BYTES)
                needs_recovery = self.query_markers_need_recovery(paths) or any((_summary_from_record(document).phase is EngineeringRunPhase.ACTIVE and owner_status(document, self._owner_liveness) is OwnerStatus.DEAD for document, _, _ in records))
                if not needs_recovery:
                    return history_projection(paths, records, prove_success)
            return cast(tuple[EngineeringRunSummaryV1, ...] | EngineeringErrorV1, self._recover_query(context))
        except (InstanceLockError, EngineeringRecordError, EngineeringStoreError, OSError, StorageMaintenanceError) as exc:
            return self._query_failure(exc, store_protocol=True)
    def result(self, run_id: str, context: EngineeringExecutionContext) -> EngineeringRunResultV1 | EngineeringErrorV1:
        try:
            validate_run_id(run_id)
        except ValueError:
            return EngineeringErrorV1(EngineeringQueryStatus.BLOCKED, 'ENGINEERING_RUN_NOT_FOUND')
        paths = context.app_paths
        if not paths.engineering_runs_dir.exists():
            return EngineeringErrorV1(EngineeringQueryStatus.BLOCKED, 'ENGINEERING_RUN_NOT_FOUND')
        try:
            paths.validate_engineering_directories()
            with InstanceLock.create(paths.engineering_store_lock_file, owner_liveness=self._owner_liveness, create_parent=False):
                path = paths.engineering_run_file(run_id)
                if not path.exists():
                    return EngineeringErrorV1(EngineeringQueryStatus.BLOCKED, 'ENGINEERING_RUN_NOT_FOUND')
                document, raw, _ = _bounded_document(path, MAX_ENGINEERING_RUN_RECORD_BYTES)
                projected = result_snapshot(paths, document, raw, lambda value: owner_status(value, self._owner_liveness), self.matching_marker_needs_recovery, prove_success)
                if projected is not None:
                    return projected
            return cast(EngineeringRunResultV1 | EngineeringErrorV1, self._recover_query(context, run_id))
        except (InstanceLockError, EngineeringRecordError, EngineeringStoreError, OSError, StorageMaintenanceError) as exc:
            return self._query_failure(exc, store_protocol=True)
    def result_for_workspace(self, run_id: str, context: EngineeringExecutionContext) -> EngineeringRunResultV1 | EngineeringErrorV1:
        workspace = context.workspace
        if workspace is None:
            return EngineeringErrorV1(EngineeringQueryStatus.BLOCKED, "ENGINEERING_RUN_NOT_FOUND")
        try:
            validate_run_id(run_id)
        except ValueError:
            return EngineeringErrorV1(EngineeringQueryStatus.BLOCKED, "ENGINEERING_RUN_NOT_FOUND")
        paths = context.app_paths
        if not paths.engineering_runs_dir.exists():
            return EngineeringErrorV1(EngineeringQueryStatus.BLOCKED, "ENGINEERING_RUN_NOT_FOUND")
        try:
            paths.validate_engineering_directories()
            with InstanceLock.create(paths.engineering_store_lock_file, owner_liveness=self._owner_liveness, create_parent=False):
                scoped = scoped_run_document(paths, run_id, workspace.workspace_id, _belongs_to_workspace)
                if scoped is None:
                    return EngineeringErrorV1(EngineeringQueryStatus.BLOCKED, "ENGINEERING_RUN_NOT_FOUND")
                document, raw = scoped
                projected = result_snapshot(paths, document, raw, lambda value: owner_status(value, self._owner_liveness), self.matching_marker_needs_recovery, prove_success)
                if projected is not None:
                    return projected
            return cast(EngineeringRunResultV1 | EngineeringErrorV1, self._recover_query(context, run_id, workspace.workspace_id))
        except (InstanceLockError, EngineeringRecordError, EngineeringStoreError, OSError, StorageMaintenanceError) as exc:
            return self._query_failure(exc, store_protocol=True)
    def _recover_query(self, context: EngineeringExecutionContext, run_id: str | None = None, scoped_workspace_id: str | None = None) -> Any:
        paths = context.app_paths
        try:
            with self._transient_recovery(paths):
                with InstanceLock.create(paths.engineering_store_lock_file, owner_liveness=self._owner_liveness, create_parent=False):
                    return recover_query_locked(self, paths, context, run_id, scoped_workspace_id)
        except (InstanceLockError, EngineeringRecordError, EngineeringStoreError, OSError) as exc:
            return self._query_failure(exc, store_protocol=False)

    @staticmethod
    def _read_run_records(paths: Any) -> list[tuple[dict[str, Any], bytes, os.stat_result]]:
        return read_run_records(paths, _RUN_FILE, MAX_ENGINEERING_RUN_RECORD_BYTES)

    @staticmethod
    def _read_run_document(paths: Any, run_id: str) -> tuple[dict[str, Any], bytes, os.stat_result]:
        return _bounded_document(paths.engineering_run_file(run_id), MAX_ENGINEERING_RUN_RECORD_BYTES)

    @staticmethod
    def _project_recovered_history(paths: Any, records: list[tuple[dict[str, Any], bytes, os.stat_result]]) -> tuple[EngineeringRunSummaryV1, ...]:
        return history_projection(paths, records, prove_success)

    @staticmethod
    def _prove_success(paths: Any, run_id: str) -> None:
        prove_success(paths, run_id)

    @staticmethod
    def _scoped_run_exists(paths: Any, run_id: str, workspace_id: str) -> bool:
        return scoped_run_exists(paths, run_id, workspace_id, _belongs_to_workspace)

    @contextmanager
    def _transient_recovery(self, paths: Any) -> Iterator[None]:
        try:
            lease = HomeLifecycleLease.begin_transient(paths.home_dir)
        except Exception as exc:
            raise EngineeringStoreError('ENGINEERING_DURABLE_STATE_INDETERMINATE') from exc
        try:
            StorageBootstrap().prepare(paths)
            paths.validate_engineering_directories()
            yield
        finally:
            lease.close()
    @staticmethod
    def _query_failure(exc: Exception, *, store_protocol: bool) -> EngineeringErrorV1:
        return query_failure(exc, store_protocol=store_protocol)

    def query_markers_need_recovery(self, paths: Any) -> bool:
        return any(run is None or self.matching_marker_needs_recovery(paths, run, raw) for _path, _marker, run, raw, _summary in self._transition_records(paths))

    def matching_marker_needs_recovery(self, paths: Any, run: dict[str, Any], raw: bytes) -> bool:
        try:
            return matching_marker_needs_recovery(paths, run, raw, lambda value: owner_status(value, self._owner_liveness), same_identity, MAX_ENGINEERING_TRANSITION_RECORD_BYTES)
        except EngineeringRecordError as exc:
            raise EngineeringStoreError("ENGINEERING_STORE_STATE_UNSAFE") from exc

    def _transition_records(self, paths: Any) -> Iterator[tuple[Path, dict[str, Any], dict[str, Any] | None, bytes, EngineeringRunSummaryV1 | None]]:
        for marker_path in self._transition_paths(paths):
            yield (marker_path, *self._transition_record_at(paths, marker_path))

    def recover_or_project_active(self, paths: Any, document: dict[str, Any], raw: bytes, context: EngineeringExecutionContext) -> EngineeringRunSummaryV1:
        return recover_or_project_active(self, paths, document, raw, context)

    def _terminalize_dead_active(self, paths: Any, document: dict[str, Any], raw: bytes, context: EngineeringExecutionContext) -> EngineeringRunSummaryV1:
        return terminalize_dead_active(paths, document, raw, context, self._owner_liveness, self._observe_reconciliation_safe_point)


def query_failure(exc: Exception, *, store_protocol: bool) -> EngineeringErrorV1:
    if isinstance(exc, InstanceLockError):
        return EngineeringErrorV1(EngineeringQueryStatus.BLOCKED, "ENGINEERING_LOCK_BUSY")
    if isinstance(exc, EngineeringRecordError):
        return EngineeringErrorV1(EngineeringQueryStatus.PROTOCOL_ERROR, "ENGINEERING_RUN_RECORD_CORRUPT")
    if isinstance(exc, EngineeringStoreError) and (
        store_protocol or exc.code in {"ENGINEERING_STORE_STATE_UNSAFE", "ENGINEERING_RUN_RECORD_CORRUPT"}
    ):
        return EngineeringErrorV1(EngineeringQueryStatus.PROTOCOL_ERROR, exc.code)
    return EngineeringErrorV1(EngineeringQueryStatus.UNVERIFIED, "ENGINEERING_DURABLE_STATE_INDETERMINATE")


def _release_lock(resource_lock: InstanceLock | None) -> None:
    if resource_lock is not None:
        resource_lock.release()
