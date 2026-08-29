"""Task result value objects retained by the runtime compatibility surface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from agent.runtime.outcome_taxonomy import OperationalStatus

TaskStatus = OperationalStatus


@dataclass(frozen=True)
class Artifact:
    kind: str
    path: Optional[str] = None
    content: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskResult:
    status: TaskStatus
    summary: str = ""
    artifacts: tuple[Artifact, ...] = ()
    diagnostics: tuple[Dict[str, Any], ...] = ()
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == TaskStatus.SUCCEEDED


__all__ = ["Artifact", "TaskResult", "TaskStatus"]
