from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.observability import (
    DiagnosticRecord,
    TraceCompleteness,
    TraceCorruptError,
    TraceStore,
)
from agent.observability import trace_writer as trace_writer_module
from agent.observability.application_adapter import finish_observation
from agent.observability.live import ObservationSession
from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_kinds import RuntimeEventKind
from agent.runtime.events import RuntimeEvent
from agent.runtime.paths import WorkspacePaths


def _paths(tmp_path: Path) -> WorkspacePaths:
    paths = WorkspacePaths("wave9-c4", tmp_path / "data", tmp_path / "state", tmp_path / "cache")
    paths.ensure_directories()
    return paths


def _event(run_id: str, timestamp: str, summary: str = "event") -> RuntimeEvent:
    correlation = RunCorrelation(run_id=run_id, root_task_id="root-c4", task_id="root-c4")
    return RuntimeEvent.from_fields(
        RuntimeEventKind.WARNING,
        correlation,
        {"summary": summary},
        timestamp=timestamp,
    )


def test_shutdown_deadline_covers_blocked_final_publication_and_late_state_stays_unclean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(
        paths,
        "blocked-finalization-c4",
        auto_start=False,
        shutdown_timeout_seconds=0,
    )
    session = ObservationSession(paths, store.run_id, trace_store=store, heartbeat_interval_seconds=60)
    session.start()

    entered_publication = threading.Event()
    release_publication = threading.Event()
    original_atomic_write = trace_writer_module._atomic_write

    def block_worker_publication(path: Any, payload: Any) -> None:
        if threading.current_thread() is store._worker:
            entered_publication.set()
            release_publication.wait()
        original_atomic_write(path, payload)

    monkeypatch.setattr(trace_writer_module, "_atomic_write", block_worker_publication)
    store.append(_event(store.run_id, "2026-09-02T12:00:00+00:00"))
    assert entered_publication.wait(2)

    canonical_result = SimpleNamespace(status="succeeded", success=True, error=None)
    application = SimpleNamespace(observation_session=session)
    finish_observation(application, canonical_result)

    assert release_publication.is_set() is False
    assert canonical_result.status == "succeeded"
    assert canonical_result.success is True
    assert canonical_result.error is None
    assert session.closed is True
    assert store.metadata.active is False
    assert store.metadata.completeness is TraceCompleteness.UNCLEAN

    release_publication.set()
    store._worker.join(timeout=2)
    assert not store._worker.is_alive()
    late_read = TraceStore.open(paths, store.run_id).read_result()
    assert late_read.completeness is TraceCompleteness.UNCLEAN
    assert late_read.completeness is not TraceCompleteness.COMPLETE


def test_active_partial_tail_preserves_known_loss_while_writer_is_active(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(
        paths,
        "active-partial-tail-c4",
        auto_start=False,
        queue_capacity=1,
        shutdown_timeout_seconds=2,
    )
    store.append_diagnostic(DiagnosticRecord(kind="pipeline_health", run_id=store.run_id))
    store.append_diagnostic(DiagnosticRecord(kind="pipeline_health", run_id=store.run_id))
    store.start()
    assert store.flush(2)
    assert store.metadata.active is True
    assert store.metadata.completeness is TraceCompleteness.PARTIAL

    reader = TraceStore.open(paths, store.run_id)
    snapshot_path = store.run_dir / "reader-snapshot.jsonl"
    snapshot_path.write_bytes(store.trace_file.read_bytes() + b'{"source":"runtime_event"')
    reader.trace_file = snapshot_path
    observed = reader.read_result()
    assert observed.partial_final_line is True
    assert observed.completeness is TraceCompleteness.PARTIAL
    assert observed.completeness is not TraceCompleteness.UNCLEAN

    snapshot_path.unlink()
    reader.trace_file = store.trace_file
    store.append(_event(store.run_id, "2026-09-02T12:00:01+00:00", "continued"))
    assert store.flush(2)
    assert reader.read_result().completeness is TraceCompleteness.PARTIAL

    closed = store.close(timeout_seconds=2)
    assert closed.active is False
    assert closed.completeness is TraceCompleteness.PARTIAL
    assert TraceStore.open(paths, store.run_id).read_result().completeness is TraceCompleteness.PARTIAL


def test_missing_persisted_trace_file_is_explicitly_corrupt(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(paths, "missing-persisted-file-c4", auto_start=False, shutdown_timeout_seconds=2)
    store.append(_event(store.run_id, "2026-09-02T12:00:00+00:00"))
    store.start()
    assert store.flush(2)
    assert store.metadata.highest_sequence_persisted > 0

    if store._file_handle is not None:
        store._file_handle.close()
        store._file_handle = None
    store.trace_file.unlink()

    reader = TraceStore.open(paths, store.run_id)
    result = reader.read_result()
    assert result.completeness is TraceCompleteness.CORRUPT
    assert any("persisted records" in issue for issue in result.issues)
    with pytest.raises(TraceCorruptError, match="persisted records"):
        reader.read_result(raise_on_corrupt=True)

    closed = store.close(timeout_seconds=2)
    assert closed.active is False


def test_active_zero_persisted_trace_without_file_is_inspectable(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(paths, "missing-empty-file-c4", auto_start=False, shutdown_timeout_seconds=2)
    reader = TraceStore.open(paths, store.run_id)
    result = reader.read_result()
    assert result.completeness is TraceCompleteness.ACTIVE
    assert result.corrupt is False
    store.close(timeout_seconds=2)
