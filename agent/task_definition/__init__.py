"""Product-owned immutable task-definition authority domain."""

from __future__ import annotations

from typing import Any

from agent.task_definition.errors import (
    TaskDefinitionBlocked,
    TaskDefinitionCompilationError,
    TaskDefinitionError,
    TaskDefinitionMismatchError,
    TaskDefinitionMissingError,
    TaskDefinitionNeedsInput,
    TaskDefinitionPersistenceError,
    TaskDefinitionValidationError,
)
from agent.task_definition.models import (
    TaskContract,
    TaskDefinitionBinding,
    TaskDefinitionRecord,
    TaskDefinitionRef,
    TaskSpec,
    TaskSpecPhase,
)


def __getattr__(name: str) -> Any:
    if name in {'TaskContextMaterialization', 'TaskContextResolver'}:
        from agent.task_definition.resolver import TaskContextMaterialization, TaskContextResolver

        return {
            'TaskContextMaterialization': TaskContextMaterialization,
            'TaskContextResolver': TaskContextResolver,
        }[name]
    if name == "TaskDefinitionCompiler":
        from agent.task_definition.compiler import TaskDefinitionCompiler

        return TaskDefinitionCompiler
    if name == "TaskDefinitionRepository":
        from agent.task_definition.repository import TaskDefinitionRepository

        return TaskDefinitionRepository
    raise AttributeError(name)


__all__ = [
    "TaskContextMaterialization",
    "TaskContextResolver",
    "TaskContract",
    "TaskDefinitionBinding",
    "TaskDefinitionBlocked",
    "TaskDefinitionCompilationError",
    "TaskDefinitionCompiler",
    "TaskDefinitionError",
    "TaskDefinitionMissingError",
    "TaskDefinitionMismatchError",
    "TaskDefinitionNeedsInput",
    "TaskDefinitionPersistenceError",
    "TaskDefinitionRecord",
    "TaskDefinitionRef",
    "TaskDefinitionRepository",
    "TaskDefinitionValidationError",
    "TaskSpec",
    "TaskSpecPhase",
]
