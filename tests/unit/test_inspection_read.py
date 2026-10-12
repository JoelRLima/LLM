from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import pytest

from llm_agent.agent.observability.trace_store import TraceCorruptError, TraceUnavailableError
from llm_agent.application.inspection import read_operations
from llm_agent.application.inspection.contracts import InspectionReadRequest
from llm_agent.application.inspection.errors import InspectionCorruptDataError, InspectionUnavailableError


def test_read_forwards_every_query_filter_once_and_projects_canonical_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    build_calls: list[dict[str, Any]] = []
    service_calls: list[tuple[str, dict[str, Any]]] = []
    projected: list[bool] = []
    query = object()

    class QueryFactory:
        @staticmethod
        def build(**kwargs: Any) -> object:
            build_calls.append(kwargs)
            return query

    class Snapshot:
        def to_dict(self) -> dict[str, Any]:
            projected.append(True)
            return {"run": {"run_id": "run"}, "timeline": []}

    class Service:
        def __init__(self, paths: object) -> None:
            assert paths == "workspace-paths"

        def snapshot(self, run_id: str | None, **kwargs: Any) -> Snapshot:
            service_calls.append((run_id or "", kwargs))
            return Snapshot()

    monkeypatch.setattr(read_operations, "InspectionQuery", QueryFactory)
    monkeypatch.setattr(read_operations, "InspectionService", Service)
    request = InspectionReadRequest(
        run_id="run-1",
        limit=17,
        after_sequence=4,
        selected_sequence=9,
        sequence_start=2,
        sequence_end=12,
        sources=["runtime"],
        event_kinds=["started"],
        activity_categories=["task"],
        severities=["warning"],
        statuses=["failed"],
        task_id="task-1",
        root_task_id="root-1",
        step=3,
        correlation_id="corr-1",
        invocation_id="inv-1",
        time_start="2026-01-01T00:00:00Z",
        time_end="2026-01-02T00:00:00Z",
        search="needle",
        bookmarked_only=True,
    )

    context = read_operations.InspectionReadContext("workspace-paths")
    result = read_operations.read_inspection(context, request)

    assert isinstance(result, Mapping)
    assert result == {"run": {"run_id": "run"}, "timeline": []}
    assert len(build_calls) == 1
    assert build_calls[0] == {
        "sequence_start": 2,
        "sequence_end": 12,
        "sources": ["runtime"],
        "event_kinds": ["started"],
        "activity_categories": ["task"],
        "severities": ["warning"],
        "statuses": ["failed"],
        "task_id": "task-1",
        "root_task_id": "root-1",
        "step": 3,
        "correlation_id": "corr-1",
        "invocation_id": "inv-1",
        "time_start": "2026-01-01T00:00:00Z",
        "time_end": "2026-01-02T00:00:00Z",
        "search": "needle",
        "bookmarked_only": True,
    }
    assert service_calls == [("run-1", {"query": query, "limit": 17, "selected_sequence": 9, "after_sequence": 4})]
    assert projected == [True]


@pytest.mark.parametrize("failure", [TypeError, ValueError])
def test_query_failure_does_not_execute_service_read(monkeypatch: pytest.MonkeyPatch, failure: type[Exception]) -> None:
    service_calls: list[str] = []

    class QueryFactory:
        @staticmethod
        def build(**_kwargs: Any) -> object:
            raise failure("invalid query")

    class Service:
        def __init__(self, _paths: object) -> None:
            service_calls.append("construct")

        def snapshot(self, *_args: object, **_kwargs: object) -> object:
            service_calls.append("snapshot")
            raise AssertionError("service read must not run after query construction fails")

    monkeypatch.setattr(read_operations, "InspectionQuery", QueryFactory)
    monkeypatch.setattr(read_operations, "InspectionService", Service)

    with pytest.raises(failure, match="invalid query"):
        context = read_operations.InspectionReadContext("paths")
        read_operations.read_inspection(context, InspectionReadRequest())
    assert service_calls == ["construct"]


@pytest.mark.parametrize("failure", [TraceUnavailableError, TraceCorruptError])
def test_trace_failures_preserve_canonical_exception_identity(monkeypatch: pytest.MonkeyPatch, failure: type[Exception]) -> None:
    class Service:
        def __init__(self, _paths: object) -> None:
            pass

        def snapshot(self, *_args: object, **_kwargs: object) -> object:
            raise failure("trace problem")

    monkeypatch.setattr(read_operations, "InspectionService", Service)

    translated = InspectionUnavailableError if failure is TraceUnavailableError else InspectionCorruptDataError
    with pytest.raises(translated, match="trace problem") as captured:
        read_operations.read_inspection(read_operations.InspectionReadContext("paths"), InspectionReadRequest())
    assert str(captured.value) == "trace problem"
    assert isinstance(captured.value.__cause__, failure)


def test_replay_uses_the_same_canonical_query_and_returns_primitive_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    built = 0

    class QueryFactory:
        @staticmethod
        def build(**_kwargs: Any) -> str:
            nonlocal built
            built += 1
            return "query"

    activity = SimpleNamespace(to_dict=lambda: {"sequence": 5, "title": "event", "summary": "safe"})
    selected = SimpleNamespace(
        metadata=SimpleNamespace(run_id="selected", to_dict=lambda: {"run_id": "selected"}),
        read_result=SimpleNamespace(completeness=SimpleNamespace(value="complete"), issues=()),
    )

    class Service:
        def __init__(self, _paths: object) -> None:
            pass

        def select(self, run_id: str | None) -> Any:
            assert run_id is None
            return selected

        def replay(self, run_id: str, **kwargs: Any) -> tuple[object, ...]:
            assert run_id == "selected"
            assert kwargs == {"query": "query", "limit": 100, "after_sequence": 2}
            return (activity,)

    monkeypatch.setattr(read_operations, "InspectionQuery", QueryFactory)
    monkeypatch.setattr(read_operations, "InspectionService", Service)
    result = read_operations.read_inspection(
        read_operations.InspectionReadContext("paths"),
        InspectionReadRequest(mode="replay", after_sequence=2),
    )

    assert built == 1
    assert result == {
        "run": {"run_id": "selected"},
        "completeness": "complete",
        "issues": [],
        "activities": [{"sequence": 5, "title": "event", "summary": "safe"}],
    }
