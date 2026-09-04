from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from agent.observability import (
    DiagnosticRecord,
    ObservationSource,
    TraceCompleteness,
    TraceRetentionPolicy,
    TraceStore,
)
from agent.observability import trace_writer as trace_writer_module
from agent.observability import trace_writer_publication as publication_module
from agent.observability.live import ObservationSession
from agent.observability.trace_catalog import TraceCatalog
from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_kinds import RuntimeEventKind
from agent.runtime.events import RuntimeEvent
from agent.runtime.paths import WorkspacePaths


def _paths(tmp_path: Path) -> WorkspacePaths:
    paths = WorkspacePaths("wave9-c6", tmp_path / "data", tmp_path / "state", tmp_path / "cache")
    paths.ensure_directories()
    return paths


def _event(run_id: str, timestamp: str = "2026-09-02T12:00:00+00:00") -> RuntimeEvent:
    correlation = RunCorrelation(run_id=run_id, root_task_id="root-c6", task_id="root-c6")
    return RuntimeEvent.from_fields(
        RuntimeEventKind.WARNING,
        correlation,
        {"summary": "corrective six"},
        timestamp=timestamp,
    )


def test_queued_envelope_wins_over_dirty_control_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TraceStore(_paths(tmp_path), "c6-queued-first", auto_start=False, shutdown_timeout_seconds=2)
    order: list[str] = []
    original_persist = store._persist_envelope
    original_publish = store._persist_dirty_metadata

    def record_persist(item: Any) -> None:
        order.append("trace")
        original_persist(item)

    def record_publish() -> None:
        order.append("metadata")
        original_publish()

    monkeypatch.setattr(store, "_persist_envelope", record_persist)
    monkeypatch.setattr(store, "_persist_dirty_metadata", record_publish)
    store.append(_event(store.run_id))
    store.start()

    assert store.flush(2)
    assert order[0] == "trace"
    assert "metadata" in order
    assert order.index("trace") < order.index("metadata")
    store.close(timeout_seconds=2)


def test_multiple_queued_envelopes_coalesce_metadata_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TraceStore(_paths(tmp_path), "c6-coalesced", auto_start=False, shutdown_timeout_seconds=2)
    publication_calls: list[str] = []
    original_write = store._write_metadata
    original_index = store._update_index

    def record_write(metadata: dict[str, Any] | None = None) -> None:
        publication_calls.append("metadata")
        original_write(metadata)

    def record_index(metadata: dict[str, Any] | None = None) -> None:
        publication_calls.append("index")
        original_index(metadata)

    monkeypatch.setattr(store, "_write_metadata", record_write)
    monkeypatch.setattr(store, "_update_index", record_index)
    for index in range(3):
        store.append(_event(store.run_id, f"2026-09-02T12:00:0{index}+00:00"))
    store.start()

    assert store.flush(2)
    assert store.metadata.highest_sequence_persisted == 3
    assert [record.sequence for record in TraceStore.open(_paths(tmp_path), store.run_id).read()] == [1, 2, 3]
    assert 0 < len(publication_calls) < 2 * 3
    assert publication_calls.count("metadata") == publication_calls.count("index")
    store.close(timeout_seconds=2)


def test_heartbeat_is_coalescible_and_cannot_starve_flush(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(paths, "c6-heartbeat", auto_start=False, shutdown_timeout_seconds=2)
    store.append(_event(store.run_id))
    entered_publication = threading.Event()
    release_publication = threading.Event()
    heartbeats_done = threading.Event()
    atomic_threads: list[threading.Thread] = []
    first_worker_publication = True
    original_atomic_write = trace_writer_module._atomic_write

    def block_worker_publication(path: Any, payload: Any) -> None:
        nonlocal first_worker_publication
        atomic_threads.append(threading.current_thread())
        if threading.current_thread() is store._worker and first_worker_publication:
            first_worker_publication = False
            entered_publication.set()
            if not release_publication.wait(2):
                raise AssertionError("publication barrier was not released")
        original_atomic_write(path, payload)

    monkeypatch.setattr(trace_writer_module, "_atomic_write", block_worker_publication)
    store.start()
    assert entered_publication.wait(2)
    with store._condition:
        revision_before_heartbeats = store._metadata_revision

    latest = "2026-01-01T00:00:03+00:00"

    def issue_heartbeats() -> None:
        for second in range(1, 4):
            store.heartbeat(f"2026-01-01T00:00:0{second}+00:00")
        heartbeats_done.set()

    heartbeat_thread = threading.Thread(target=issue_heartbeats, name="c6-heartbeats")
    heartbeat_thread.start()
    try:
        assert heartbeats_done.wait(1)
        heartbeat_thread.join(timeout=1)
        with store._condition:
            assert store._metadata_revision == revision_before_heartbeats
            assert store._metadata_values["last_observer_heartbeat"] == latest
        assert all(thread is store._worker for thread in atomic_threads)
        release_publication.set()
        assert store.flush(2)
        persisted = TraceStore.open(paths, store.run_id).metadata
        assert persisted.last_observer_heartbeat == latest
    finally:
        release_publication.set()
        heartbeat_thread.join(timeout=2)
        store.close(timeout_seconds=2)


def test_timeout_zero_late_publication_remains_unclean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(paths, "c6-timeout-zero", auto_start=False, shutdown_timeout_seconds=0)
    session = ObservationSession(
        paths,
        store.run_id,
        trace_store=store,
        retention_policy=TraceRetentionPolicy(max_runs=1, max_bytes=1, max_age_seconds=0),
        heartbeat_interval_seconds=60,
    )
    session.start()
    entered_publication = threading.Event()
    release_publication = threading.Event()
    retention_calls: list[object] = []
    original_atomic_write = trace_writer_module._atomic_write
    original_retention = TraceCatalog.apply_retention

    def block_worker_publication(path: Any, payload: Any) -> None:
        if threading.current_thread() is store._worker:
            entered_publication.set()
            if not release_publication.wait(2):
                raise AssertionError("publication barrier was not released")
        original_atomic_write(path, payload)

    def observe_retention(catalog: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        retention_calls.append(catalog)
        return original_retention(catalog, *args, **kwargs)

    monkeypatch.setattr(trace_writer_module, "_atomic_write", block_worker_publication)
    monkeypatch.setattr(TraceCatalog, "apply_retention", observe_retention)
    store.append(_event(store.run_id))
    assert entered_publication.wait(2)

    metadata = session.finish(timeout_seconds=0)
    assert metadata.completeness is TraceCompleteness.UNCLEAN
    assert store.finalization_settled is False
    assert retention_calls == []
    assert store.run_dir.exists()

    release_publication.set()
    store._worker.join(timeout=2)
    assert not store._worker.is_alive()
    persisted = TraceStore.open(paths, store.run_id).read_result()
    assert persisted.completeness is TraceCompleteness.UNCLEAN
    assert persisted.completeness is not TraceCompleteness.COMPLETE


def test_partial_stays_partial_when_writer_settles(tmp_path: Path) -> None:
    store = TraceStore(
        _paths(tmp_path),
        "c6-partial",
        queue_capacity=1,
        auto_start=False,
        shutdown_timeout_seconds=2,
    )
    store.append_diagnostic(DiagnosticRecord(kind="pipeline_health", run_id=store.run_id))
    store.append_diagnostic(DiagnosticRecord(kind="pipeline_health", run_id=store.run_id))
    store.start()

    metadata = store.close(timeout_seconds=2)
    assert metadata.completeness is TraceCompleteness.PARTIAL
    assert metadata.completeness is not TraceCompleteness.UNCLEAN
    assert metadata.dropped_count >= 1
    records = TraceStore.open(_paths(tmp_path), store.run_id).read_result().records
    assert any(record.source is ObservationSource.GAP for record in records)


def test_timeout_prevents_new_dequeue_after_current_inflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(paths, "c6-timeout-queue", auto_start=False, shutdown_timeout_seconds=2)
    first_started = threading.Event()
    release_first = threading.Event()
    persisted_sequences: list[int] = []
    original_persist = store._persist_envelope

    def block_first(item: Any) -> None:
        if not first_started.is_set():
            first_started.set()
            if not release_first.wait(2):
                raise AssertionError("first envelope barrier was not released")
        persisted_sequences.append(item.envelope.sequence)
        original_persist(item)

    monkeypatch.setattr(store, "_persist_envelope", block_first)
    store.append(_event(store.run_id, "2026-09-02T12:00:01+00:00"))
    store.append(_event(store.run_id, "2026-09-02T12:00:02+00:00"))
    store.start()
    assert first_started.wait(2)
    with store._condition:
        assert store._inflight is True
        assert len(store._pending) == 1

    metadata = store.close(timeout_seconds=0)
    assert metadata.completeness is TraceCompleteness.UNCLEAN
    with store._condition:
        assert store._finalization_timed_out is True
        assert len(store._pending) == 1

    try:
        release_first.set()
        store._worker.join(timeout=2)
        assert not store._worker.is_alive()
        assert persisted_sequences == [1]
        persisted = TraceStore.open(paths, store.run_id).read_result()
        assert [record.sequence for record in persisted.records] == [1]
        assert persisted.completeness is TraceCompleteness.UNCLEAN
    finally:
        release_first.set()
        store._worker.join(timeout=2)


def test_heartbeat_during_bounded_followup_remains_dirty_until_durable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(paths, "c6-heartbeat-followup", auto_start=False, shutdown_timeout_seconds=2)
    store.append(_event(store.run_id))
    entered_first = threading.Event()
    release_first = threading.Event()
    entered_followup = threading.Event()
    release_followup = threading.Event()
    snapshot_threads: list[threading.Thread] = []
    original_write_snapshot = publication_module._write_snapshot

    def block_two_snapshots(owner: Any, metadata: dict[str, Any], *, final: bool) -> None:
        snapshot_index = len(snapshot_threads)
        snapshot_threads.append(threading.current_thread())
        if snapshot_index == 0:
            entered_first.set()
            if not release_first.wait(2):
                raise AssertionError("first snapshot barrier was not released")
        elif snapshot_index == 1:
            entered_followup.set()
            if not release_followup.wait(2):
                raise AssertionError("follow-up snapshot barrier was not released")
        original_write_snapshot(owner, metadata, final=final)

    monkeypatch.setattr(publication_module, "_write_snapshot", block_two_snapshots)
    store.start()
    assert entered_first.wait(2)
    heartbeat_one = "2026-01-01T00:00:01+00:00"
    heartbeat_two = "2026-01-01T00:00:02+00:00"
    store.heartbeat(heartbeat_one)
    release_first.set()
    assert entered_followup.wait(2)
    store.heartbeat(heartbeat_two)
    release_followup.set()

    try:
        assert store.flush(timeout_seconds=2)
        persisted = TraceStore.open(paths, store.run_id).metadata
        assert persisted.last_observer_heartbeat == heartbeat_two
        with store._condition:
            assert store._metadata_values["last_observer_heartbeat"] == heartbeat_two
            assert store._metadata_dirty is False
            assert store._publication_in_flight == 0
        assert len(snapshot_threads) >= 3
        assert all(thread is store._worker for thread in snapshot_threads)
    finally:
        release_first.set()
        release_followup.set()
        store.close(timeout_seconds=2)


def test_heartbeat_after_bounded_recovery_rearms_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(paths, "c6-heartbeat-rearm", auto_start=False, shutdown_timeout_seconds=2)
    store.append(_event(store.run_id))
    entered_first = threading.Event()
    release_first = threading.Event()
    entered_followup = threading.Event()
    release_followup = threading.Event()
    entered_recovery = threading.Event()
    release_recovery = threading.Event()
    recovery_snapshot_finished = threading.Event()
    snapshot_threads: list[threading.Thread] = []
    atomic_threads: list[threading.Thread] = []
    original_write_snapshot = publication_module._write_snapshot
    original_atomic_write = trace_writer_module._atomic_write

    def block_three_snapshots(owner: Any, metadata: dict[str, Any], *, final: bool) -> None:
        snapshot_index = len(snapshot_threads)
        snapshot_threads.append(threading.current_thread())
        if snapshot_index == 0:
            entered_first.set()
            if not release_first.wait(2):
                raise AssertionError("first snapshot barrier was not released")
        elif snapshot_index == 1:
            entered_followup.set()
            if not release_followup.wait(2):
                raise AssertionError("follow-up snapshot barrier was not released")
        elif snapshot_index == 2:
            entered_recovery.set()
            if not release_recovery.wait(2):
                raise AssertionError("recovery snapshot barrier was not released")
        original_write_snapshot(owner, metadata, final=final)
        if snapshot_index == 2:
            recovery_snapshot_finished.set()

    def record_atomic_write(path: Any, payload: Any) -> None:
        atomic_threads.append(threading.current_thread())
        original_atomic_write(path, payload)

    monkeypatch.setattr(publication_module, "_write_snapshot", block_three_snapshots)
    monkeypatch.setattr(trace_writer_module, "_atomic_write", record_atomic_write)
    store.start()
    assert entered_first.wait(2)
    heartbeat_one = "2026-01-01T00:00:01+00:00"
    heartbeat_two = "2026-01-01T00:00:02+00:00"
    heartbeat_three = "2026-01-01T00:00:03+00:00"
    heartbeat_four = "2026-01-01T00:00:04+00:00"
    store.heartbeat(heartbeat_one)
    release_first.set()
    assert entered_followup.wait(2)
    store.heartbeat(heartbeat_two)
    release_followup.set()
    assert entered_recovery.wait(2)
    store.heartbeat(heartbeat_three)
    release_recovery.set()

    try:
        assert recovery_snapshot_finished.wait(2)
        assert store.flush(timeout_seconds=2)
        with store._condition:
            assert store._publication_in_flight == 0
            assert store._inflight is False
            assert store._metadata_dirty is True
            assert store._heartbeat_quiesced is True

        store.heartbeat(heartbeat_four)
        with store._condition:
            assert store._heartbeat_quiesced is False
        assert store.flush(timeout_seconds=2)
        persisted = TraceStore.open(paths, store.run_id).metadata
        assert persisted.last_observer_heartbeat == heartbeat_four
        with store._condition:
            assert store._metadata_values["last_observer_heartbeat"] == heartbeat_four
            assert store._metadata_dirty is False
            assert store._publication_in_flight == 0
        assert len(snapshot_threads) >= 4
        assert atomic_threads
        assert all(thread is store._worker for thread in snapshot_threads)
        assert all(thread is store._worker for thread in atomic_threads)
    finally:
        release_first.set()
        release_followup.set()
        release_recovery.set()
        store.close(timeout_seconds=2)
