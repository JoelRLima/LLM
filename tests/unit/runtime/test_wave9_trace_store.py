from __future__ import annotations

from pathlib import Path

from agent.observability import (
    DiagnosticRecord,
    ObservationSource,
    TraceCompleteness,
    TraceRetentionPolicy,
    TraceStore,
    safe_run_key,
)
from agent.runtime.correlation import RunCorrelation
from agent.runtime.events import RuntimeEvent
from agent.runtime.paths import WorkspacePaths


def _paths(tmp_path: Path) -> WorkspacePaths:
    paths = WorkspacePaths("wave9", tmp_path / "data", tmp_path / "state", tmp_path / "cache")
    paths.ensure_directories()
    return paths


def _event(run_id: str, timestamp: str, kind: str = "task_node_started") -> RuntimeEvent:
    correlation = RunCorrelation(run_id=run_id, root_task_id="root", task_id="root")
    return RuntimeEvent.from_fields(kind, correlation, timestamp=timestamp)


def test_trace_store_assigns_sequence_and_replays_by_sequence(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(paths, "run/with/unsafe-id", root_task_id="root", shutdown_timeout_seconds=2)
    store.append(_event(store.run_id, "2026-09-02T12:00:02Z"))
    store.append(_event(store.run_id, "2026-09-02T12:00:01Z", "task_outcome"))
    assert store.flush(2)
    metadata = store.close()
    assert metadata.completeness is TraceCompleteness.COMPLETE
    assert metadata.highest_sequence_persisted == 2
    assert store.run_dir.name == safe_run_key(store.run_id)
    reader = TraceStore.open(paths, store.run_id)
    records = reader.read()
    assert [record.sequence for record in records] == [1, 2]
    assert [record.timestamp for record in records] == [
        "2026-09-02T12:00:02Z",
        "2026-09-02T12:00:01Z",
    ]


def test_active_reader_sees_prefix_and_ignores_partial_final_line(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(paths, "run-active", shutdown_timeout_seconds=2)
    store.append(_event(store.run_id, "2026-09-02T12:00:00Z"))
    assert store.flush(2)
    with store.trace_file.open("ab") as handle:
        handle.write(b'{"source":"runtime_event"')
    reader = TraceStore.open(paths, store.run_id)
    result = reader.read_result()
    assert [record.sequence for record in result.records] == [1]
    assert result.partial_final_line is True
    assert result.completeness is TraceCompleteness.ACTIVE
    store.close()


def test_malformed_complete_line_is_corrupt_and_never_executed(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(paths, "run-corrupt", shutdown_timeout_seconds=2)
    store.append(_event(store.run_id, "2026-09-02T12:00:00Z"))
    assert store.flush(2)
    store.close()
    with store.trace_file.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("not-json\n")
    reader = TraceStore.open(paths, store.run_id)
    result = reader.read_result()
    assert result.completeness is TraceCompleteness.CORRUPT
    assert result.records[0].sequence == 1


def test_diagnostic_suppression_is_not_loss_or_semantic_event(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(paths, "run-diagnostics", mode="NORMAL", shutdown_timeout_seconds=2)
    sequence = store.append_diagnostic(
        DiagnosticRecord(kind="transport", minimum_mode="TRACE", run_id=store.run_id)
    )
    assert sequence is None
    assert store.metadata.suppressed_count == 1
    assert store.metadata.semantic_count == 0
    store.close()


def test_gap_marker_and_partial_completeness_are_explicit_under_pressure(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(paths, "run-pressure", queue_capacity=1, auto_start=False, shutdown_timeout_seconds=2)
    store.append_diagnostic(DiagnosticRecord(kind="pipeline_health", run_id=store.run_id))
    store.append_diagnostic(DiagnosticRecord(kind="pipeline_health", run_id=store.run_id))
    store.start()
    metadata = store.close()
    assert metadata.completeness is TraceCompleteness.PARTIAL
    reader = TraceStore.open(paths, store.run_id)
    records = reader.read()
    assert any(item.source is ObservationSource.GAP for item in records)
    assert metadata.dropped_count >= 1


def test_loss_marks_partial_while_writer_stays_active_and_is_retained(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(paths, "run-live-partial", queue_capacity=1, auto_start=False, shutdown_timeout_seconds=2)
    store.append_diagnostic(DiagnosticRecord(kind="pipeline_health", run_id=store.run_id))
    store.append_diagnostic(DiagnosticRecord(kind="pipeline_health", run_id=store.run_id))

    metadata = store.metadata
    assert metadata.active is True
    assert metadata.completeness is TraceCompleteness.PARTIAL

    reader = TraceStore.open(paths, store.run_id)
    assert reader.read_result().completeness is TraceCompleteness.ACTIVE
    assert TraceStore.for_workspace(paths).latest().run_id == store.run_id
    assert (
        store.run_id
        not in TraceStore.for_workspace(paths).apply_retention(
            TraceRetentionPolicy(max_runs=1, max_bytes=1, max_age_seconds=0),
            now=2_000_000_000,
        )
    )

    store.start()
    assert store.flush(2)
    assert store.metadata.active is True
    assert store.metadata.completeness is TraceCompleteness.PARTIAL
    assert reader.read_result().completeness is TraceCompleteness.PARTIAL

    closed = store.close()
    assert closed.active is False
    assert closed.completeness is TraceCompleteness.PARTIAL
    assert TraceStore.open(paths, store.run_id).read_result().completeness is TraceCompleteness.PARTIAL


def test_catalog_and_retention_keep_active_runs(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    active = TraceStore(paths, "run-active-retention", shutdown_timeout_seconds=2)
    old = TraceStore(paths, "run-old-retention", shutdown_timeout_seconds=2)
    old.append(_event(old.run_id, "2020-01-01T00:00:00+00:00"))
    old.close()
    catalog = TraceStore.for_workspace(paths)
    removed = catalog.apply_retention(
        TraceRetentionPolicy(max_runs=1, max_bytes=64 * 1024 * 1024, max_age_seconds=0),
        now=2_000_000_000,
    )
    assert old.run_id in removed
    assert active.run_id not in removed
    assert catalog.latest().run_id == active.run_id
    active.close()
