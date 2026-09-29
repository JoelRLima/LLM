from __future__ import annotations

from pathlib import Path

from llm_agent.agent.application import AgentApplication
from llm_agent.application.context import AppPaths
from llm_agent.application.services.composition import build_application_services
from llm_agent.application.services.queries import ReadOnlyWorkspaceQueryService
from llm_agent.outputs.service import OutputService
from llm_agent.workspace.context import WorkspaceContext


def test_application_composes_workspace_services_outside_agent_facade(tmp_path: Path) -> None:
    application = AgentApplication.__new__(AgentApplication)
    application.workspace = WorkspaceContext.create(tmp_path)
    application.workspace_paths = AppPaths.discover(app_home=tmp_path / "home", env={}).for_workspace(
        application.workspace.workspace_id
    )
    services = build_application_services(application.workspace, application.workspace_paths)
    assert isinstance(services.query_service, ReadOnlyWorkspaceQueryService)
    assert services.query_service.workspace.root == application.workspace.root
    assert isinstance(services.output_service, OutputService)
    assert services.output_service.workspace_paths == application.workspace_paths
    assert not hasattr(application, "workspace_query_service")
    assert not hasattr(application, "output_service")
    assert not hasattr(application, "action_catalog")
