from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ErrorCategory(Enum):
    FILE_NOT_FOUND = "FileNotFoundError"
    SANDBOX = "SandboxError"
    SCHEMA = "SchemaError"
    TOOL_BLOCKED = "ToolBlocked"
    TIMEOUT = "TimeoutError"
    UNKNOWN = "Unknown"


@dataclass
class ReplanContext:
    task: str
    current_step: Dict[str, Any]
    tool_history: List[Dict[str, Any]]
    heuristic_replans: int = 0
    llm_replans: int = 0
    last_exception: Optional[str] = None
    last_tool_result: Optional[Dict[str, Any]] = None
    budget_remaining: Optional[int] = None
    # The task owns this mapping.  A context is an attempt view, never the
    # retry ledger itself.
    retry_counts: MutableMapping[str, int] | None = None

    def count(self, kind: str) -> int:
        if self.retry_counts is not None:
            return int(self.retry_counts.get(kind, 0))
        return self.heuristic_replans if kind == "heuristic" else self.llm_replans

    def record(self, kind: str) -> None:
        if self.retry_counts is not None:
            self.retry_counts[kind] = self.count(kind) + 1
            self.retry_counts["total"] = self.count("total") + 1
        if kind == "heuristic":
            self.heuristic_replans += 1
        else:
            self.llm_replans += 1


@dataclass
class ReplanAction:
    steps: List[Dict[str, Any]] = field(default_factory=list)
    source: str = ""
    reason: str = ""


class RetryPolicy:
    def __init__(self, max_total: int = 2, max_heuristic: int = 2, max_llm: int = 1):
        self.max_total = max_total
        self.max_heuristic = max_heuristic
        self.max_llm = max_llm

    def allows_heuristic(self, context: ReplanContext) -> bool:
        total = context.count("total") if context.retry_counts is not None else context.count("heuristic") + context.count("llm")
        heuristic = context.count("heuristic")
        return total < self.max_total and heuristic < self.max_heuristic

    def allows_llm(self, context: ReplanContext) -> bool:
        total = context.count("total") if context.retry_counts is not None else context.count("heuristic") + context.count("llm")
        llm = context.count("llm")
        return total < self.max_total and llm < self.max_llm


def classify_error(error_message: str) -> ErrorCategory:
    message = (error_message or "").lower()
    patterns = (
        (ErrorCategory.FILE_NOT_FOUND, ("filenotfounderror", "arquivo não encontrado", "no such file")),
        (ErrorCategory.SANDBOX, ("sandbox", "fail-closed", "traversal", "absoluto")),
        (ErrorCategory.SCHEMA, ("schema", "campo obrigatório", "argumentos inválidos")),
        (ErrorCategory.TOOL_BLOCKED, ("não permitida", "não está permitida")),
        (ErrorCategory.TIMEOUT, ("timeout", "excedeu")),
    )
    for category, terms in patterns:
        if any(term in message for term in terms):
            return category
    return ErrorCategory.UNKNOWN
