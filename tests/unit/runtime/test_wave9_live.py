from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent.observability import ObservationSession, SilenceLevel, SilencePolicy
from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_dispatch import RuntimeEventDispatcher
from agent.runtime.event_kinds import RuntimeEventKind
from agent.runtime.events import RuntimeEvent
from agent.runtime.paths import WorkspacePaths


def _paths(tmp_path: Path) -> WorkspacePaths:
    paths = WorkspacePaths("live", tmp_path / "data", tmp_path / "state", tmp_path / "cache")
    paths.ensure_directories()
    return paths


def test_session_attaches_through_dispatcher_and_detaches_read_only(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    correlation = RunCorrelation.fresh()
    dispatcher = RuntimeEventDispatcher()
    session = ObservationSession.for_correlation(
        paths,
        correlation,
        heartbeat_interval_seconds=0.05,
    )
    session.start(dispatcher)
    dispatcher.emit(
        RuntimeEvent.from_fields(
            RuntimeEventKind.TASK_NODE_STARTED,
            correlation,
            {"summary": "started"},
            timestamp="2026-01-01T00:00:00+00:00",
        )
    )
    assert session.store.flush(1.0)
    attachment = session.attach()
    assert attachment.tail(limit=10)
    attachment.detach()
    assert attachment.detached is True
    before = session.metadata
    attachment.detach()
    assert session.metadata.highest_sequence_accepted == before.highest_sequence_accepted
    session.finish({"status": "succeeded"})
    assert session.closed is True
    assert all(item is not session for item in dispatcher._sinks)


def test_fake_clock_reports_heartbeat_and_semantic_silence_separately(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    correlation = RunCorrelation.fresh()
    current = [datetime(2026, 1, 1, 0, 0, 20, tzinfo=timezone.utc)]
    session = ObservationSession.for_correlation(
        paths,
        correlation,
        clock=lambda: current[0],
        silence_policy=SilencePolicy(quiet_after_seconds=5, warning_after_seconds=10, stale_after_seconds=20),
        heartbeat_interval_seconds=1,
    )
    session.store.append(
        RuntimeEvent.from_fields(
            RuntimeEventKind.TASK_NODE_STARTED,
            correlation,
            timestamp="2026-01-01T00:00:00+00:00",
        )
    )
    assert session.store.flush(1.0)
    session.store.heartbeat("2026-01-01T00:00:18+00:00")
    status = session.silence_status()
    assert status.level is SilenceLevel.STALE
    assert status.elapsed_seconds == 20
    assert status.heartbeat_age_seconds == 2
    assert status.canonical_watchdog is None
