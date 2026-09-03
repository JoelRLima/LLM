from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from agent.observability import TraceCompleteness, TraceStore
from agent.observability import trace_writer as trace_writer_module
from agent.presentation import InspectionQuery, InspectionService
from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_kinds import RuntimeEventKind
from agent.runtime.events import RuntimeEvent
from agent.runtime.paths import WorkspacePaths


def _paths(tmp_path: Path) -> WorkspacePaths:
    paths = WorkspacePaths("wave9-c3", tmp_path / "data", tmp_path / "state", tmp_path / "cache")
    paths.ensure_directories()
    return paths


def _event(run_id: str, timestamp: str, *, summary: str = "event") -> RuntimeEvent:
    correlation = RunCorrelation(
        run_id=run_id,
        root_task_id="root-c3",
        task_id="task-c3",
        parent_task_id="parent-c3",
    )
    return RuntimeEvent.from_fields(RuntimeEventKind.WARNING, correlation, {"summary": summary}, timestamp=timestamp)


def test_persistent_writer_failure_latches_without_recursive_gap_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(
        paths,
        "writer-failure-c3",
        auto_start=False,
        queue_capacity=4,
        shutdown_timeout_seconds=1,
    )
    attempted: list[int] = []
    publication_attempted = threading.Event()

    def fail_persistence(item: Any) -> None:
        attempted.append(item.envelope.sequence)
        publication_attempted.set()
        raise OSError("persistent trace sink failure")

    monkeypatch.setattr(store, "_persist_envelope", fail_persistence)
    first = store.append(_event(store.run_id, "2026-09-02T12:00:00+00:00"))
    second = store.append(_event(store.run_id, "2026-09-02T12:00:01+00:00"))
    assert first == 1
    assert second == 2

    store.start()
    assert publication_attempted.wait(2)
    store.emit(_event(store.run_id, "2026-09-02T12:00:02+00:00"))
    assert store.flush(1) is False
    store._worker.join(timeout=1)
    assert not store._worker.is_alive()
    assert attempted == [first]
    assert not store._pending
    assert not store._pending_gaps
    assert store._writer_error == "OSError"
    assert store.metadata.highest_sequence_accepted == 3
    assert store.metadata.gap_count == 0
    assert store.metadata.dropped_count >= 2

    closed = store.close(timeout_seconds=1)
    assert closed.active is False
    assert closed.completeness is TraceCompleteness.UNCLEAN
    persisted = TraceStore.open(paths, store.run_id).read_result()
    assert persisted.completeness is TraceCompleteness.UNCLEAN
    assert attempted == [first]


def test_producer_accept_is_not_blocked_by_writer_metadata_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(
        paths,
        "publication-barrier-c3",
        auto_start=False,
        shutdown_timeout_seconds=2,
    )
    publication_entered = threading.Event()
    release_publication = threading.Event()
    original_atomic_write = trace_writer_module._atomic_write

    def block_worker_publication(path: Any, payload: Any) -> None:
        if threading.current_thread() is store._worker:
            publication_entered.set()
            assert release_publication.wait(2)
        original_atomic_write(path, payload)

    monkeypatch.setattr(trace_writer_module, "_atomic_write", block_worker_publication)
    store.append(_event(store.run_id, "2026-09-02T12:00:00+00:00", summary="first"))
    store.start()
    assert publication_entered.wait(2)

    accepted = threading.Event()
    result: dict[str, int | None] = {}

    def producer() -> None:
        result["sequence"] = store.append(_event(store.run_id, "2026-09-02T12:00:01+00:00", summary="second"))
        accepted.set()

    producer_thread = threading.Thread(target=producer, name="wave9-c3-producer")
    producer_thread.start()
    assert accepted.wait(1)
    assert result["sequence"] == 2

    release_publication.set()
    producer_thread.join(timeout=1)
    assert not producer_thread.is_alive()
    assert store.flush(2)
    closed = store.close(timeout_seconds=2)
    assert closed.completeness is TraceCompleteness.COMPLETE


def test_time_windows_compare_normalized_instants_and_include_boundaries(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    run_id = "time-window-c3"
    store = TraceStore(paths, run_id, shutdown_timeout_seconds=2)
    store.append(_event(run_id, "2026-01-01T00:00:00+00:00", summary="utc-earlier"))
    store.append(_event(run_id, "2025-12-31T23:30:00-03:00", summary="offset-later"))
    store.close()
    service = InspectionService(paths)

    equivalent = InspectionQuery.build(
        time_start="2025-12-31T21:00:00-03:00",
        time_end="2025-12-31T21:00:00-03:00",
    )
    assert [item.sequence for item in service.query(run_id, query=equivalent)] == [1]

    lexical_later = InspectionQuery.build(
        time_start="2025-12-31T20:00:00-03:00",
        time_end="2025-12-31T23:30:00-03:00",
    )
    assert [item.sequence for item in service.query(run_id, query=lexical_later)] == [1, 2]

    lexical_earlier = InspectionQuery.build(
        time_start="2026-01-01T00:30:00+00:00",
        time_end="2026-01-01T03:00:00+00:00",
    )
    assert [item.sequence for item in service.query(run_id, query=lexical_earlier)] == [2]


def test_time_window_input_rejects_invalid_and_inverted_ranges() -> None:
    with pytest.raises(ValueError, match="valid ISO timestamp"):
        InspectionQuery.build(time_start="not-a-timestamp")
    with pytest.raises(ValueError, match="must not precede"):
        InspectionQuery.build(
            time_start="2026-01-01T00:00:01Z",
            time_end="2025-12-31T20:00:00-03:00",
        )


def test_cli_time_window_invalid_input_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from agent.interfaces.cli.app import main

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = main(
        [
            "inspect",
            "show",
            "--json",
            "--workspace",
            str(workspace),
            "--time-start",
            "not-a-timestamp",
        ]
    )
    assert result == 2
    assert "valid ISO timestamp" in capsys.readouterr().out
