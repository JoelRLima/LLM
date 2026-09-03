from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.interfaces.cli import inspector as inspector_module
from agent.interfaces.cli.inspector import _query
from agent.interfaces.cli.parser import build_parser
from agent.observability import (
    DiagnosticRecord,
    TraceCompleteness,
    TraceRetentionPolicy,
    TraceStore,
)
from agent.observability import trace_writer as trace_writer_module
from agent.observability.live import ObservationSession
from agent.observability.trace_catalog import TraceCatalog
from agent.presentation import InspectionQuery, InspectionService
from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_kinds import RuntimeEventKind
from agent.runtime.events import RuntimeEvent
from agent.runtime.paths import WorkspacePaths


def _paths(tmp_path: Path) -> WorkspacePaths:
    paths = WorkspacePaths("wave9-c2", tmp_path / "data", tmp_path / "state", tmp_path / "cache")
    paths.ensure_directories()
    return paths


def _correlation(run_id: str = "run-c2") -> RunCorrelation:
    return RunCorrelation(
        run_id=run_id,
        root_task_id="root-c2",
        task_id="task-c2",
        parent_task_id="parent-c2",
    )


def _event(
    correlation: RunCorrelation,
    kind: RuntimeEventKind,
    timestamp: str,
    **data: object,
) -> RuntimeEvent:
    return RuntimeEvent.from_fields(kind, correlation, data, timestamp=timestamp)


def test_stale_active_marker_is_explicit_and_not_selected_as_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    now = [datetime(2026, 1, 10, tzinfo=timezone.utc)]
    stale = TraceStore(paths, "stale-c2", clock=lambda: old, auto_start=False)
    closed = TraceStore(paths, "closed-c2", clock=lambda: now[0], auto_start=False)
    closed.close()

    service = InspectionService(paths, clock=lambda: now[0])
    listed = {item.run_id: item for item in service.list_runs(limit=10)}
    stale_summary = listed[stale.run_id]
    assert stale_summary.active is True
    assert stale_summary.liveness["state"] == "stale"
    assert stale_summary.liveness["certainty"] == "uncertain"
    assert stale_summary.liveness["definitely_live"] is False
    assert service.select().metadata.run_id == closed.run_id

    snapshot = service.snapshot(stale.run_id, limit=10)
    assert snapshot.run.liveness == stale_summary.liveness
    assert snapshot.heartbeat["observer_heartbeat"] is None
    assert snapshot.heartbeat["liveness"]["state"] == "stale"
    assert "hung" not in json.dumps(snapshot.to_dict(), ensure_ascii=False).casefold()

    args = SimpleNamespace(
        json_output=False,
        after=0,
        limit=10,
        inspect_sequence=None,
    )
    monkeypatch.setattr(inspector_module, "_is_tty", lambda: True)
    monkeypatch.setattr(inspector_module.console, "clear", lambda: None)
    monkeypatch.setattr(inspector_module, "render_snapshot", lambda *_args, **_kwargs: None)
    assert inspector_module._run_follow(service, stale.run_id, args) == 0
    removed = TraceCatalog(paths).apply_retention(
        TraceRetentionPolicy(max_runs=1, max_bytes=1, max_age_seconds=0),
        now=now[0],
    )
    assert stale.run_id not in removed
    assert stale.run_dir.exists()


def test_observation_session_boundary_applies_retention_without_manual_call(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    current = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    policy = TraceRetentionPolicy(
        max_runs=1,
        max_bytes=64 * 1024 * 1024,
        max_age_seconds=365 * 24 * 60 * 60,
    )
    first = ObservationSession(
        paths,
        "retention-first-c2",
        clock=lambda: current[0],
        retention_policy=policy,
    )
    first.store.append(_event(_correlation(first.run_id), RuntimeEventKind.TASK_NODE_STARTED, current[0].isoformat()))
    first.finish()

    current[0] += timedelta(seconds=1)
    second = ObservationSession(
        paths,
        "retention-second-c2",
        clock=lambda: current[0],
        retention_policy=policy,
    )
    second.store.append(_event(_correlation(second.run_id), RuntimeEventKind.TASK_NODE_STARTED, current[0].isoformat()))
    second.finish()

    retained = {item.run_id for item in TraceCatalog(paths).list_runs(limit=10)}
    assert first.run_id not in retained
    assert second.run_id in retained


def test_retention_failure_does_not_change_finalized_trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _paths(tmp_path)
    session = ObservationSession(paths, "retention-failure-c2")

    def fail_retention(*_args: Any, **_kwargs: Any) -> tuple[str, ...]:
        raise OSError("retention unavailable")

    monkeypatch.setattr(TraceCatalog, "apply_retention", fail_retention)
    metadata = session.finish()
    assert metadata.active is False
    assert metadata.completeness is TraceCompleteness.COMPLETE


def test_drop_defers_atomic_publication_to_writer_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(paths, "drop-sentinel-c2", queue_capacity=1, auto_start=False, shutdown_timeout_seconds=2)
    calls: list[tuple[str, str]] = []
    original_atomic_write = trace_writer_module._atomic_write

    def sentinel_atomic_write(path: Any, payload: Any) -> None:
        calls.append((threading.current_thread().name, str(path)))
        original_atomic_write(path, payload)

    monkeypatch.setattr(trace_writer_module, "_atomic_write", sentinel_atomic_write)
    store.append_diagnostic(DiagnosticRecord(kind="pipeline_health", run_id=store.run_id))
    store.append_diagnostic(DiagnosticRecord(kind="pipeline_health", run_id=store.run_id))
    assert calls == []
    assert store.metadata.active is True
    assert store.metadata.completeness is TraceCompleteness.PARTIAL

    reader = TraceStore.open(paths, store.run_id)
    assert reader.read_result().completeness is TraceCompleteness.ACTIVE
    store.start()
    assert store.flush(2)
    assert calls
    assert all(name != threading.current_thread().name for name, _ in calls)
    assert reader.read_result().completeness is TraceCompleteness.PARTIAL
    assert store.metadata.active is True
    store.close()


def test_cli_forwards_complete_bounded_filter_surface(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    correlation = _correlation()
    store = TraceStore(paths, correlation.run_id, root_task_id=correlation.root_task_id)
    store.append(
        _event(
            correlation,
            RuntimeEventKind.TOOL_START,
            "2026-01-01T00:00:01+00:00",
            tool="reader",
            correlation_id="corr-c2",
            status="started",
            invocation_id="inv-c2",
            step=2,
        )
    )
    store.append(
        _event(
            correlation,
            RuntimeEventKind.STEP_FAILED,
            "2026-01-01T00:00:02+00:00",
            status="failed",
            step=3,
        )
    )
    store.append_diagnostic(
        DiagnosticRecord(
            kind="pipeline_health",
            severity="warning",
            timestamp="2026-01-01T00:00:03+00:00",
            message="observer warning",
            correlation=correlation,
        )
    )
    store.append(
        _event(
            correlation,
            RuntimeEventKind.WARNING,
            "2026-01-01T00:00:04+00:00",
            summary="safe warning",
        )
    )
    store.close()

    from agent.observability.bookmarks import BookmarkStore

    BookmarkStore(paths).add(store.run_id, 1, "bounded note")
    service = InspectionService(paths)
    assert [item.sequence for item in service.query(store.run_id, query=InspectionQuery.build(sources=["semantic"]))] == [1, 2, 4]
    assert [item.sequence for item in service.query(store.run_id, query=InspectionQuery.build(activity_categories=["tool"]))] == [1]
    assert [item.sequence for item in service.query(store.run_id, query=InspectionQuery.build(statuses=["failed"]))] == [2]
    assert [item.sequence for item in service.query(store.run_id, query=InspectionQuery.build(correlation_id="corr-c2"))] == [1]
    assert [item.sequence for item in service.query(store.run_id, query=InspectionQuery.build(task_id=correlation.task_id))] == [1, 2, 3, 4]
    assert [item.sequence for item in service.query(store.run_id, query=InspectionQuery.build(root_task_id=correlation.root_task_id))] == [1, 2, 3, 4]
    assert [item.sequence for item in service.query(store.run_id, query=InspectionQuery.build(step=2))] == [1]
    assert [item.sequence for item in service.query(store.run_id, query=InspectionQuery.build(invocation_id="inv-c2"))] == [1]
    assert [item.sequence for item in service.query(store.run_id, query=InspectionQuery.build(time_start="2026-01-01T00:00:02+00:00", time_end="2026-01-01T00:00:03+00:00"))] == [2, 3]
    assert [item.sequence for item in service.query(store.run_id, query=InspectionQuery.build(bookmarked_only=True))] == [1]
    assert [item.sequence for item in service.query(store.run_id, query=InspectionQuery.build(search="safe warning"))] == [4]

    parser = build_parser()
    args = parser.parse_args(
        [
            "inspect",
            "show",
            "--after",
            "4",
            "--sequence-start",
            "2",
            "--sequence-end",
            "4",
            "--source",
            "semantic",
            "--category",
            "tool",
            "--status",
            "failed",
            "--task-id",
            correlation.task_id,
            "--root-task-id",
            correlation.root_task_id,
            "--step",
            "2",
            "--correlation-id",
            "corr-c2",
            "--invocation-id",
            "inv-c2",
            "--time-start",
            "2026-01-01T00:00:01+00:00",
            "--time-end",
            "2026-01-01T00:00:04+00:00",
            "--bookmarked-only",
            "--search",
            "safe",
        ]
    )
    query = _query(args)
    assert query.to_dict() == {
        "sequence_start": 2,
        "sequence_end": 4,
        "sources": ["semantic"],
        "event_kinds": [],
        "activity_categories": ["tool"],
        "severities": [],
        "statuses": ["failed"],
        "task_id": correlation.task_id,
        "root_task_id": correlation.root_task_id,
        "step": 2,
        "correlation_id": "corr-c2",
        "invocation_id": "inv-c2",
        "time_start": "2026-01-01T00:00:01+00:00",
        "time_end": "2026-01-01T00:00:04+00:00",
        "search": "safe",
        "bookmarked_only": True,
    }


def test_live_filtered_follow_advances_by_observed_sequence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _paths(tmp_path)
    current = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    correlation = _correlation("filtered-follow-c2")
    store = TraceStore(
        paths,
        "filtered-follow-c2",
        clock=lambda: current[0],
        auto_start=False,
        shutdown_timeout_seconds=2,
    )
    store.append_diagnostic(DiagnosticRecord(kind="pipeline_health", run_id=store.run_id))
    store.start()
    assert store.flush(2)
    service = InspectionService(paths, clock=lambda: current[0])
    args = SimpleNamespace(
        json_output=False,
        after=0,
        limit=1,
        inspect_sequence=None,
        inspect_sources=["semantic"],
    )
    calls: list[int] = []
    rendered: list[tuple[int, ...]] = []
    real_snapshot = service.snapshot

    def snapshot(*positional: object, **keyword: object) -> Any:
        calls.append(int(keyword["after_sequence"]))
        value = real_snapshot(*positional, **keyword)
        if len(calls) == 1:
            store.append(_event(correlation, RuntimeEventKind.WARNING, current[0].isoformat(), summary="new"))
            assert store.flush(2)
        elif len(calls) == 3:
            raise KeyboardInterrupt
        return value

    monkeypatch.setattr(service, "snapshot", snapshot)
    monkeypatch.setattr(inspector_module, "_is_tty", lambda: True)
    monkeypatch.setattr(inspector_module.console, "clear", lambda: None)
    monkeypatch.setattr(inspector_module, "time", SimpleNamespace(sleep=lambda _seconds: None))
    monkeypatch.setattr(
        inspector_module,
        "render_snapshot",
        lambda value: rendered.append(tuple(item.sequence for item in value.timeline)),
    )

    assert inspector_module._run_follow(service, store.run_id, args) == 0
    assert calls == [0, 1, 2]
    assert rendered[:2] == [(), (2,)]
    store.close()
