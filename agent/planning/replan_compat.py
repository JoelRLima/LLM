"""Explicit compatibility edge for pre-Wave-2 replan callers.

These helpers are not used by the canonical recovery path. They preserve old
test/admin construction shapes while keeping the task-owned canonical budget
out of the compatibility object.
"""

from __future__ import annotations

from typing import Any, MutableMapping

from agent.runtime.failures import FailureFact, failure_fact_from_legacy_message


class LegacyReplanContext:
    """Legacy mutable projection retained only for external compatibility."""

    def __init__(
        self,
        task: str,
        current_step: dict[str, Any],
        tool_history: list[dict[str, Any]],
        heuristic_replans: int = 0,
        llm_replans: int = 0,
        last_exception: str | None = None,
        last_tool_result: dict[str, Any] | None = None,
        budget_remaining: int | None = None,
        retry_counts: MutableMapping[str, int] | None = None,
    ) -> None:
        self.task = task
        self.current_step = current_step
        self.tool_history = tool_history
        self.heuristic_replans = heuristic_replans
        self.llm_replans = llm_replans
        self.last_exception = last_exception
        self.last_tool_result = last_tool_result
        self.budget_remaining = budget_remaining
        self.retry_counts = retry_counts

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


class RetryPolicy:
    """Legacy facade; canonical production replan ignores this object."""

    def __init__(self, max_total: int = 2, max_heuristic: int = 2, max_llm: int = 1):
        self.max_total = max_total
        self.max_heuristic = max_heuristic
        self.max_llm = max_llm

    def allows_heuristic(self, context: LegacyReplanContext) -> bool:
        total = (
            context.count("total")
            if context.retry_counts is not None
            else context.count("heuristic") + context.count("llm")
        )
        return total < self.max_total and context.count("heuristic") < self.max_heuristic

    def allows_llm(self, context: LegacyReplanContext) -> bool:
        total = (
            context.count("total")
            if context.retry_counts is not None
            else context.count("heuristic") + context.count("llm")
        )
        return total < self.max_total and context.count("llm") < self.max_llm


def legacy_replan_context(*args: Any, **kwargs: Any) -> LegacyReplanContext:
    return LegacyReplanContext(*args, **kwargs)


def legacy_replan_failure(value: FailureFact | str | None) -> FailureFact:
    """Decode only the historical serialized FileNotFound exception prefix.

    This is a compatibility wire-format adapter, not a general text
    classifier. All other free text remains an unknown failure.
    """

    if isinstance(value, str) and value.startswith("FileNotFoundError:"):
        return FailureFact.from_code("FILE_NOT_FOUND", message=value)
    return value if isinstance(value, FailureFact) else failure_fact_from_legacy_message(value)


__all__ = [
    "LegacyReplanContext",
    "RetryPolicy",
    "legacy_replan_context",
    "legacy_replan_failure",
]
