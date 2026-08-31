"""Model-free CLI adapter for reading persisted task authority."""

from __future__ import annotations

from typing import Any, Callable

from agent.runtime.workspace_context import WorkspaceContext
from agent.task_definition.repository import TaskDefinitionRepository
from agent.task_definition.resolver import TaskContextResolver


def run_task_context(
    args: Any,
    *,
    app_paths: Any,
    workspace: Any,
    print_json: Callable[[Any], None],
) -> int:
    workspace_context = WorkspaceContext.create(workspace)
    workspace_paths = app_paths.for_workspace(workspace_context.workspace_id)
    repository = TaskDefinitionRepository(workspace_paths)
    materialization = TaskContextResolver(repository).resolve(
        str(args.task_id),
        phase_id=getattr(args, "phase_id", None),
    )
    if bool(getattr(args, "json_output", False)):
        print_json(materialization.to_dict())
    else:
        print(materialization.trusted_text)
    return 0
