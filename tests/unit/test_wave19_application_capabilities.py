from __future__ import annotations

from pathlib import Path

from agent.application import AgentApplication
from agent.application_services.queries import ReadOnlyWorkspaceQueryService
from agent.runtime.workspace_context import WorkspaceContext


def test_application_exposes_one_lazily_cached_neutral_query_service(tmp_path: Path) -> None:
    application = AgentApplication.__new__(AgentApplication)
    application.workspace = WorkspaceContext.create(tmp_path)
    application._workspace_query_service = None
    first = application.workspace_query_service()
    second = application.workspace_query_service()
    assert isinstance(first, ReadOnlyWorkspaceQueryService)
    assert first is second
    assert first.workspace is application.workspace
