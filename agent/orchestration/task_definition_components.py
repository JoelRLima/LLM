"""Construction of the canonical task-definition components."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent.runtime.paths import WorkspacePaths
from agent.task_definition.compiler import TaskDefinitionCompiler
from agent.task_definition.repository import TaskDefinitionRepository
from agent.task_definition.resolver import TaskContextResolver

TaskDefinitionComponents = tuple[
    TaskDefinitionRepository,
    TaskContextResolver,
    TaskDefinitionCompiler,
]
TaskDefinitionHandles = tuple[
    TaskDefinitionRepository | None,
    TaskContextResolver | None,
    TaskDefinitionCompiler | None,
]


def build_task_definition_components(
    workspace_paths: WorkspacePaths | None,
    context_manager_provider: Callable[[], Any],
) -> TaskDefinitionComponents | None:
    if workspace_paths is None:
        return None
    repository = TaskDefinitionRepository(workspace_paths)
    resolver = TaskContextResolver(repository)
    compiler = TaskDefinitionCompiler(
        repository,
        context_manager_provider=context_manager_provider,
    )
    return repository, resolver, compiler


def task_definition_handles(
    workspace_paths: WorkspacePaths | None,
    context_manager_provider: Callable[[], Any],
) -> TaskDefinitionHandles:
    components = build_task_definition_components(workspace_paths, context_manager_provider)
    return components if components is not None else (None, None, None)
