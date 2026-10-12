from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from llm_agent.agent.observability.trace_store import TraceCorruptError, TraceUnavailableError
from llm_agent.application.inspection import (
    InspectionListRequest,
    InspectionReadContext,
    list_inspection_runs,
    read_operations,
)
from llm_agent.application.inspection.errors import InspectionCorruptDataError, InspectionUnavailableError


def test_list_request_is_frozen_and_slotted() -> None:
    request = InspectionListRequest()
    assert request.limit == 100
    assert not hasattr(request, "__dict__")
    with pytest.raises(FrozenInstanceError):
        request.__setattr__("limit", 2)


def test_list_uses_context_service_once_and_canonical_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    constructed: list[object] = []
    limits: list[int] = []
    projections: list[bool] = []

    class Summary:
        def to_dict(self) -> dict[str, Any]:
            projections.append(True)
            return {"run_id": "run-1", "mode": "full"}

    class Service:
        def __init__(self, paths: object) -> None:
            constructed.append(paths)

        def list_runs(self, *, limit: int) -> tuple[Summary, ...]:
            limits.append(limit)
            return (Summary(),)

    monkeypatch.setattr(read_operations, "InspectionService", Service)
    context = InspectionReadContext("workspace-paths")
    result = list_inspection_runs(context, InspectionListRequest(limit=17))

    assert constructed == ["workspace-paths"]
    assert limits == [17]
    assert projections == [True]
    assert result == ({"run_id": "run-1", "mode": "full"},)
    assert not isinstance(result[0], Summary)


@pytest.mark.parametrize(
    ("failure", "translated"),
    [
        (TraceUnavailableError, InspectionUnavailableError),
        (TraceCorruptError, InspectionCorruptDataError),
    ],
)
def test_list_translates_only_canonical_trace_errors(
    monkeypatch: pytest.MonkeyPatch,
    failure: type[Exception],
    translated: type[Exception],
) -> None:
    original = failure("list trace problem")

    class Service:
        def __init__(self, _paths: object) -> None:
            pass

        def list_runs(self, *, limit: int) -> tuple[object, ...]:
            assert limit == 100
            raise original

    monkeypatch.setattr(read_operations, "InspectionService", Service)
    with pytest.raises(translated, match="list trace problem") as captured:
        list_inspection_runs(InspectionReadContext("paths"), InspectionListRequest())
    assert str(captured.value) == str(original)
    assert captured.value.__cause__ is original
