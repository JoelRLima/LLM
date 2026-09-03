from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.observability import (
    DiagnosticRecord,
    ObservationEnvelope,
    ObservationSource,
    TraceStore,
)
from agent.observability.live import SilenceLevel, SilencePolicy
from agent.presentation import ActivityProjection, InspectionQuery, InspectionService
from agent.runtime.correlation import RunCorrelation
from agent.runtime.event_kinds import RuntimeEventKind
from agent.runtime.events import RuntimeEvent
from agent.runtime.paths import WorkspacePaths


def _paths(tmp_path: Path) -> WorkspacePaths:
    paths = WorkspacePaths("presentation", tmp_path / "data", tmp_path / "state", tmp_path / "cache")
    paths.ensure_directories()
    return paths


def _event(kind: RuntimeEventKind, correlation: RunCorrelation, **data: object) -> RuntimeEvent:
    return RuntimeEvent.from_fields(kind, correlation, data)


def test_projection_covers_every_current_runtime_kind() -> None:
    correlation = RunCorrelation.fresh()
    activities = tuple(
        ActivityProjection.project(
            ObservationEnvelope.runtime_event(_event(kind, correlation), index + 1)
        )
        for index, kind in enumerate(RuntimeEventKind)
    )
    assert len(activities) == len(tuple(RuntimeEventKind))
    assert all(item.title and item.category and item.source == "runtime_event" for item in activities)
    assert [item.sequence for item in activities] == list(range(1, len(activities) + 1))


def test_unknown_future_kind_degrades_without_crash() -> None:
    envelope = ObservationEnvelope(
        ObservationSource.RUNTIME_EVENT,
        1,
        "future-run",
        {"type": "future_kind", "data": {"summary": "safe"}},
    )
    activity = ActivityProjection.project(envelope)
    assert activity.kind == "future_kind"
    assert activity.title == "Unknown Runtime Event"
    assert activity.summary == "safe"


def test_snapshot_filters_and_marks_missing_canonical_sources(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    correlation = RunCorrelation.fresh()
    store = TraceStore(paths, correlation.run_id, root_task_id=correlation.root_task_id)
    store.append(_event(RuntimeEventKind.MODEL_CALL_STARTED, correlation, model="safe-model"))
    store.append(_event(RuntimeEventKind.STEP_FAILED, correlation, status="failed", step=2))
    store.append_diagnostic(DiagnosticRecord("pipeline_health", message="observer okay", run_id=correlation.run_id))
    store.close()

    service = InspectionService(paths)
    query = InspectionQuery.build(event_kinds=["step_failed"])
    snapshot = service.snapshot(correlation.run_id, query=query, limit=10)
    assert [item.kind for item in snapshot.timeline] == ["step_failed"]
    assert snapshot.changes["status"] == "unavailable"
    assert snapshot.metrics["status"] == "unavailable"
    assert snapshot.to_json() == snapshot.to_json()


def test_query_uses_redacted_projection_for_search(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    correlation = RunCorrelation.fresh()
    store = TraceStore(paths, correlation.run_id, root_task_id=correlation.root_task_id, mode="trace")
    store.append(_event(RuntimeEventKind.WARNING, correlation, api_key="secret-value", summary="safe warning"))
    store.close()

    service = InspectionService(paths)
    assert len(service.query(correlation.run_id, query=InspectionQuery.build(search="secret-value"))) == 0
    assert len(service.query(correlation.run_id, query=InspectionQuery.build(search="safe warning"))) == 1


def test_silence_policy_separates_heartbeat_and_watchdog() -> None:
    policy = SilencePolicy(quiet_after_seconds=5, warning_after_seconds=10, stale_after_seconds=20)
    status = policy.evaluate(
        last_semantic_activity="2026-01-01T00:00:00+00:00",
        last_observer_heartbeat="2026-01-01T00:00:18+00:00",
        now="2026-01-01T00:00:20+00:00",
        canonical_watchdog="watchdog-event",
    )
    assert status.level is SilenceLevel.STALE
    assert status.elapsed_seconds == 20
    assert status.heartbeat_age_seconds == 2
    assert status.canonical_watchdog == "watchdog-event"


def test_snapshot_is_json_safe_and_detail_is_redacted(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    correlation = RunCorrelation.fresh()
    store = TraceStore(paths, correlation.run_id, root_task_id=correlation.root_task_id, mode="trace")
    sequence = store.append(_event(RuntimeEventKind.ERROR, correlation, password="secret", summary="failed"))
    store.close()
    assert sequence is not None
    service = InspectionService(paths)
    detail = service.detail(correlation.run_id, sequence)
    assert detail is not None
    encoded = json.dumps(detail, ensure_ascii=False)
    assert "secret" not in encoded
    assert service.snapshot(correlation.run_id, selected_sequence=sequence).selected_detail is not None


def test_query_bounds_and_invalid_range() -> None:
    assert len(InspectionQuery.build(search="x" * 1000).search or "") <= 256
    with pytest.raises(ValueError):
        InspectionQuery.build(sequence_start=5, sequence_end=2)


def test_standalone_service_and_installed_cli_project_persisted_sections(tmp_path: Path, capsys) -> None:
    from agent.interfaces.cli.app import main
    from agent.runtime.paths import AppPaths
    from agent.runtime.workspace_context import WorkspaceContext

    app_home = tmp_path / "app-home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AppPaths.discover(app_home=app_home).for_workspace(WorkspaceContext.create(workspace).workspace_id)
    paths.ensure_directories()
    correlation = RunCorrelation.fresh()
    store = TraceStore(paths, correlation.run_id, root_task_id=correlation.root_task_id, mode="trace")
    store.append(
        _event(
            RuntimeEventKind.PLAN_CREATED,
            correlation,
            plan=[
                {"step_id": "step-1", "tool": "file_reader"},
                {"step_id": "step-2", "tool": "file_writer"},
            ],
            steps=2,
        )
    )
    store.append(
        _event(
            RuntimeEventKind.MODEL_CALL_STARTED,
            correlation,
            operation="plan",
            provider="test-provider",
            call_number=1,
        )
    )
    store.append(
        _event(
            RuntimeEventKind.MODEL_CALL_COMPLETED,
            correlation,
            operation="plan",
            provider="test-provider",
            call_number=1,
            success=True,
            duration_ms=3,
            metrics={"input": 2, "output": 1},
        )
    )
    store.append(_event(RuntimeEventKind.TOOL_START, correlation, tool="file_reader", invocation_id="inv-1"))
    store.append(
        _event(
            RuntimeEventKind.TOOL_END,
            correlation,
            tool="file_reader",
            invocation_id="inv-1",
            status="succeeded",
            ok=True,
        )
    )
    store.append(_event(RuntimeEventKind.VALIDATION_REPAIR, correlation, step=1, strategy="deterministic"))
    store.append(_event(RuntimeEventKind.REPLAN, correlation, original_step=1, replacement_steps=1))
    store.append(
        _event(
            RuntimeEventKind.TASK_OUTCOME,
            correlation,
            status="succeeded",
            changes=[{"path": "safe.txt", "kind": "modify"}],
            metrics={"duration_ms": 3},
        )
    )
    store.close()

    service = InspectionService(paths)
    snapshot = service.snapshot(correlation.run_id, limit=100)
    assert snapshot.plan_steps["status"] == "available"
    assert snapshot.plan_steps["steps"]
    assert snapshot.model_calls["status"] == "available"
    assert snapshot.tools["status"] == "available"
    assert snapshot.validation["status"] == "available"
    assert snapshot.recovery["status"] == "available"
    assert snapshot.changes["status"] == "available"
    assert snapshot.metrics["status"] == "available"
    assert snapshot.plan_steps != snapshot.validation

    assert main(
        [
            "inspect",
            "show",
            "--json",
            "--home",
            str(app_home),
            "--workspace",
            str(workspace),
            "--run-id",
            correlation.run_id,
        ]
    ) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["plan_steps"] == snapshot.to_dict()["plan_steps"]
    assert shown["validation"] == snapshot.to_dict()["validation"]
    assert shown["plan_steps"] != shown["validation"]
