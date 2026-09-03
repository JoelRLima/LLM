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


def test_flush_waits_for_metadata_publication_to_settle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(
        paths,
        "flush-publication-c5",
        auto_start=False,
        shutdown_timeout_seconds=2,
    )
    entered_wait = threading.Event()
    finished = threading.Event()
    result: dict[str, bool] = {}
    real_wait = store._condition.wait

    def observe_wait(timeout: float | None = None) -> bool:
        entered_wait.set()
        return real_wait(timeout)

    monkeypatch.setattr(store._condition, "wait", observe_wait)
    with store._condition:
        store._publication_in_flight = 1

    def flush_worker() -> None:
        result["clean"] = store.flush(timeout_seconds=2)
        finished.set()

    thread = threading.Thread(target=flush_worker, name="wave9-c5-flush")
    thread.start()
    assert entered_wait.wait(1)
    assert finished.is_set() is False

    with store._condition:
        store._publication_in_flight = 0
        store._condition.notify_all()

    assert finished.wait(1)
    thread.join(timeout=1)
    assert result["clean"] is True
    monkeypatch.undo()
    store.close(timeout_seconds=2)


def test_close_reserves_terminalization_budget_after_initial_drain_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    store = TraceStore(
        paths,
        "terminal-reserve-c5",
        auto_start=False,
        shutdown_timeout_seconds=2,
    )
    budgets: list[float | None] = []

    def controlled_flush(timeout_seconds: float | None = None) -> bool:
        budgets.append(timeout_seconds)
        return len(budgets) > 1

    monkeypatch.setattr(store, "flush", controlled_flush)
    metadata = store.close(timeout_seconds=2)

    assert budgets[0] == pytest.approx(1.5)
    assert budgets[1] is not None and budgets[1] > 0
    assert metadata.active is False
    assert metadata.completeness is TraceCompleteness.UNCLEAN
    assert store.metadata.active is False
    assert store.metadata.completeness is TraceCompleteness.UNCLEAN
    assert store._worker.is_alive() is False


def test_observation_session_prioritizes_store_before_heartbeat_join() -> None:
    def build_session() -> tuple[ObservationSession, list[tuple[str, object]]]:
        calls: list[tuple[str, object]] = []
        state: dict[str, object] = {"detached": False, "session": None}

        class Store:
            _shutdown_timeout_seconds = 2.0
            finalization_settled = False

            def close(self, *, timeout_seconds: float | None = None) -> object:
                session = state["session"]
                assert isinstance(session, ObservationSession)
                calls.append(
                    (
                        "store.close",
                        (timeout_seconds, bool(session._stop.is_set()), bool(state["detached"])),
                    )
                )
                return object()

        class Dispatcher:
            def remove_sink(self, session: ObservationSession) -> None:
                state["detached"] = True
                calls.append(("dispatcher.detach", session._stop.is_set()))

        class Heartbeat:
            def is_alive(self) -> bool:
                return True

            def join(self, *, timeout: float | None = None) -> None:
                calls.append(("heartbeat.join", timeout))

        session = ObservationSession(
            object(),
            "session-order-c5",
            trace_store=Store(),
        )
        state["session"] = session
        session._dispatcher = Dispatcher()
        session._heartbeat_thread = Heartbeat()  # type: ignore[assignment]
        return session, calls

    session, calls = build_session()
    session.finish(timeout_seconds=2)
    assert [name for name, _value in calls] == [
        "dispatcher.detach",
        "store.close",
        "heartbeat.join",
    ]
    assert calls[0][1] is True
    store_timeout, stop_set, detached = calls[1][1]  # type: ignore[misc]
    assert store_timeout > 0
    assert stop_set is True
    assert detached is True
    assert calls[2][1] >= 0

    zero_session, zero_calls = build_session()
    zero_session.finish(timeout_seconds=0)
    zero_store_timeout, _zero_stop_set, _zero_detached = zero_calls[1][1]  # type: ignore[misc]
    assert zero_store_timeout == 0
    assert zero_calls[2][1] == 0
