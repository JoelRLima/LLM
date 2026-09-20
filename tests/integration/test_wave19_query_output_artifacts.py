from __future__ import annotations

from pathlib import Path

from agent.application_services.queries import (
    WorkspaceQueryKind,
    WorkspaceQueryResult,
    WorkspaceQueryStatus,
)
from agent.interfaces.cli.output_projection import (
    format_workspace_query_result,
    publish_workspace_query_result,
)
from agent.outputs.service import OutputService
from agent.runtime.paths import AppPaths


def test_query_projection_preserves_canonical_truth_and_uses_one_formatter(tmp_path: Path) -> None:
    result = WorkspaceQueryResult(
        WorkspaceQueryKind.READ,
        WorkspaceQueryStatus.SUCCEEDED,
        data={"path": "sample.txt", "content": "x" * 8_001, "truncated": True},
        truncated=True,
    )
    before = result
    service = OutputService(AppPaths.discover(tmp_path / "home", env={}).for_workspace("workspace"))
    publication = publish_workspace_query_result(service, result, action_id="query.read")
    assert result == before
    assert publication is not None and publication.artifact is not None
    assert service.store.read(publication.artifact.output_id) == format_workspace_query_result(result)
    assert publication.artifact.source_truncated is True


def test_failed_and_cancelled_queries_are_not_artifacts(tmp_path: Path) -> None:
    service = OutputService(AppPaths.discover(tmp_path / "home", env={}).for_workspace("workspace"))
    failed = WorkspaceQueryResult(
        WorkspaceQueryKind.READ,
        WorkspaceQueryStatus.FAILED,
        reason_code="QUERY_IO_FAILED",
        error="no",
    )
    assert publish_workspace_query_result(service, failed, action_id="query.read") is None
    assert service.list() == ()
