"""Stable fail-closed errors for product-owned task definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class TaskDefinitionError(RuntimeError):
    """Base error raised when task-definition authority cannot be trusted."""

    code = "TASK_DEFINITION_INVALID"
    preserve_checkpoint = True

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        self.detail = str(detail)
        self.reason_code = code or self.code
        super().__init__(self.detail)


class TaskDefinitionValidationError(TaskDefinitionError, ValueError):
    """A Contract, Spec, reference, or manifest is structurally invalid."""

    code = "TASK_DEFINITION_INVALID"


class TaskDefinitionMissingError(TaskDefinitionError, FileNotFoundError):
    """A referenced task definition is absent."""

    code = "TASK_DEFINITION_MISSING"

    def __init__(self, task_id: str, *, path: str | Path | None = None) -> None:
        self.task_id = task_id
        self.path = Path(path) if path is not None else None
        detail = f"Definição de tarefa ausente para '{task_id}'."
        if self.path is not None:
            detail = f"{detail} Caminho: {self.path}"
        TaskDefinitionError.__init__(self, detail, code=self.code)


class TaskDefinitionMismatchError(TaskDefinitionError):
    """A reference, workspace, Contract, or Spec binding contradicts."""

    code = "TASK_DEFINITION_MISMATCH"


class TaskDefinitionPersistenceError(TaskDefinitionError):
    """A durable immutable authority could not be safely persisted."""

    code = "TASK_DEFINITION_PERSISTENCE_FAILED"


class TaskDefinitionCompilationError(TaskDefinitionError):
    """A typed Contract/Spec model decision could not be admitted."""

    code = "TASK_DEFINITION_COMPILATION_FAILED"

    def __init__(
        self,
        detail: str,
        *,
        code: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        self.cause = cause
        super().__init__(detail, code=code)


class TaskDefinitionNeedsInput(TaskDefinitionCompilationError):
    """The admitted Contract decision explicitly asks the user for input."""

    code = "TASK_DEFINITION_NEEDS_INPUT"

    def __init__(self, reason: str, question: str) -> None:
        self.reason = str(reason)
        self.question = str(question)
        super().__init__(
            f"{self.reason}\nPergunta necessária: {self.question}",
            code=self.code,
        )


class TaskDefinitionBlocked(TaskDefinitionCompilationError):
    """The admitted Spec decision explicitly blocked expansion."""

    code = "TASK_DEFINITION_BLOCKED"

    def __init__(self, reason: str) -> None:
        self.reason = str(reason)
        super().__init__(self.reason, code=self.code)


def public_task_definition_error(error: Any) -> str:
    """Return a stable, non-secret public diagnostic for a definition error."""

    code = str(getattr(error, "reason_code", getattr(error, "code", "TASK_DEFINITION_INVALID")))
    detail = str(getattr(error, "detail", error))
    return f"{code}: {detail}"


__all__ = [
    "TaskDefinitionBlocked",
    "TaskDefinitionCompilationError",
    "TaskDefinitionError",
    "TaskDefinitionMissingError",
    "TaskDefinitionMismatchError",
    "TaskDefinitionNeedsInput",
    "TaskDefinitionPersistenceError",
    "TaskDefinitionValidationError",
    "public_task_definition_error",
]
