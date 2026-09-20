from __future__ import annotations

from types import MappingProxyType

import pytest

from agent.application_services.queries import (
    QUERY_CANCELLED,
    WorkspaceQueryKind,
    WorkspaceQueryRequest,
    WorkspaceQueryResult,
    WorkspaceQueryStatus,
)


def test_query_contract_has_exact_kinds_and_frozen_shapes() -> None:
    assert [kind.value for kind in WorkspaceQueryKind] == [
        "list_files",
        "read",
        "find",
        "git_status",
        "diff",
    ]
    assert WorkspaceQueryRequest.__dataclass_params__.frozen is True
    assert WorkspaceQueryResult.__dataclass_params__.frozen is True


def test_request_defensively_freezes_arguments() -> None:
    original = {"paths": ["a.py"], "nested": {"enabled": True}}
    request = WorkspaceQueryRequest(WorkspaceQueryKind.DIFF, original)
    original["paths"].append("b.py")
    original["nested"]["enabled"] = False

    assert isinstance(request.arguments, MappingProxyType)
    assert request.arguments["paths"] == ("a.py",)
    assert request.arguments["nested"]["enabled"] is True
    with pytest.raises(TypeError):
        request.arguments["new"] = "value"  # type: ignore[index]


def test_result_invariants_are_fail_closed() -> None:
    succeeded = WorkspaceQueryResult(WorkspaceQueryKind.READ, WorkspaceQueryStatus.SUCCEEDED, data="ok")
    assert succeeded.reason_code is None and succeeded.error is None
    cancelled = WorkspaceQueryResult(
        WorkspaceQueryKind.READ,
        WorkspaceQueryStatus.CANCELLED,
        reason_code=QUERY_CANCELLED,
    )
    assert cancelled.data is None
    with pytest.raises(ValueError):
        WorkspaceQueryResult(WorkspaceQueryKind.READ, WorkspaceQueryStatus.SUCCEEDED, error="bad")
    with pytest.raises(ValueError):
        WorkspaceQueryResult(WorkspaceQueryKind.READ, WorkspaceQueryStatus.FAILED)
    with pytest.raises(ValueError):
        WorkspaceQueryResult(WorkspaceQueryKind.READ, WorkspaceQueryStatus.CANCELLED, reason_code="OTHER")


def test_request_rejects_non_enum_kind() -> None:
    with pytest.raises(TypeError):
        WorkspaceQueryRequest("read", {})  # type: ignore[arg-type]
