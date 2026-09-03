from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from agent.observability import TraceCompleteness, TraceRetentionPolicy, TraceStore
from agent.observability import trace_writer as trace_writer_module
from agent.observability.live import ObservationSession
from agent.observability.trace_catalog import TraceCatalog
from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_kinds import RuntimeEventKind
from agent.runtime.events import RuntimeEvent
from agent.runtime.paths import WorkspacePaths


def _paths(tmp_path: Path) -> WorkspacePaths:
    paths = WorkspacePaths("wave9-c5", tmp_path / "data", tmp_path / "state", tmp_path / "cache")
    paths.ensure_directories()
    return paths


def _event(run_id: str) -> RuntimeEvent:
    correlation = RunCorrelation(run_id=run_id, root_task_id="root-c5", task_id="root-c5")
    return RuntimeEvent.from_fields(
        RuntimeEventKind.WARNING,
        correlation,
        {"summary": "retention boundary"},
        timestamp="2026-09-02T12:00:00+00:00",
    )


def test_retention_skips_unsettled_late_finalization_and_never_deletes_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(
        paths,
        "retention-blocked-c5",
        auto_start=False,
        shutdown_timeout_seconds=0,
    )
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
            assert release_publication.wait(2)
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
    assert release_publication.is_set() is False

    release_publication.set()
    store._worker.join(timeout=2)
    assert not store._worker.is_alive()
    persisted = TraceStore.open(paths, store.run_id).read_result()
    assert persisted.completeness is TraceCompleteness.UNCLEAN
    assert persisted.completeness is not TraceCompleteness.COMPLETE
    assert persisted.completeness is not TraceCompleteness.CORRUPT
    assert store.run_dir.exists()


def test_clean_settled_close_still_runs_automatic_retention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    calls: list[object] = []
    original_retention = TraceCatalog.apply_retention

    def observe_retention(catalog: Any, *args: Any, **kwargs: Any) -> tuple[str, ...]:
        calls.append(catalog)
        return original_retention(catalog, *args, **kwargs)

    monkeypatch.setattr(TraceCatalog, "apply_retention", observe_retention)
    store = TraceStore(paths, "retention-clean-c5", shutdown_timeout_seconds=2)
    session = ObservationSession(
        paths,
        store.run_id,
        trace_store=store,
        retention_policy=TraceRetentionPolicy(),
        heartbeat_interval_seconds=60,
    )

    metadata = session.finish()
    assert metadata.completeness is TraceCompleteness.COMPLETE
    assert store.finalization_settled is True
    assert len(calls) == 1
    assert store.run_dir.exists()
