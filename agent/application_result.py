"""Structured result returned by the standalone application boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentRunResult:
    """Structured boundary result shared by headless interfaces."""

    status: str
    answer: str
    workspace: str
    error: str | None = None
    diagnostics: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    receipt: dict[str, Any] = field(default_factory=dict)
    report_path: str | None = None

    @property
    def success(self) -> bool:
        return self.status == "succeeded"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "success": self.success,
            "answer": self.answer,
            "workspace": self.workspace,
            "error": self.error,
            "diagnostics": list(self.diagnostics),
            "metadata": dict(self.metadata),
            "receipt": dict(self.receipt),
            "report_path": self.report_path,
        }
