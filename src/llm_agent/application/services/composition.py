"""Workspace-scoped Product Application services for interface consumers."""

from __future__ import annotations

from dataclasses import dataclass

from llm_agent.application.services.queries import ReadOnlyWorkspaceQueryService
from llm_agent.outputs.service import OutputService
from llm_agent.workspace.context import WorkspaceContext
from llm_agent.workspace.paths import WorkspacePaths


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    query_service: ReadOnlyWorkspaceQueryService
    output_service: OutputService


def build_application_services(
    workspace: WorkspaceContext,
    workspace_paths: WorkspacePaths,
) -> ApplicationServices:
    """Construct interface services for one canonical workspace."""

    if workspace.workspace_id != workspace_paths.workspace_id:
        raise ValueError("application services require matching workspace paths")
    return ApplicationServices(
        query_service=ReadOnlyWorkspaceQueryService(workspace),
        output_service=OutputService(workspace_paths),
    )


__all__ = ["ApplicationServices", "build_application_services"]
