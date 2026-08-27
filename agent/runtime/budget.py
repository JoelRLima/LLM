from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from threading import Lock
from typing import Any

from agent.llm.contracts import normalize_usage
from agent.runtime.budget_estimation import (
    estimate_model_request_allowance,
    estimate_model_request_tokens,
    estimate_payload_allowance,
    estimate_payload_tokens,
)
from agent.runtime.limits import default_runtime_limit_values, runtime_limit_values


class BudgetExhausted(RuntimeError):
    code = "TASK_BUDGET_EXHAUSTED"

    def __init__(self, resource: str, limit: int, used: int) -> None:
        self.resource = resource
        self.limit = limit
        self.used = used
        super().__init__(f"Task budget exhausted for {resource}: {used}/{limit}.")

@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    model_calls: int
    tool_calls: int
    reported_input_tokens: int
    reported_output_tokens: int
    reported_total_tokens: int
    model_calls_with_reported_total: int
    estimated_tokens: int
    accounted_tokens: int
    reserved_tokens: int
    model_calls_with_reported_usage: int
    model_calls_without_reported_usage: int
    token_usage_complete: bool
    max_model_calls: int
    max_task_tool_calls: int
    max_task_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def _limit(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value

class TaskBudgetLedger:
    def __init__(
        self,
        max_model_calls: int | None = None,
        max_task_tool_calls: int | None = None,
        max_task_tokens: int | None = None,
    ) -> None:
        defaults = default_runtime_limit_values()
        self.max_model_calls = _limit(
            defaults["max_model_calls"] if max_model_calls is None else max_model_calls,
            "max_model_calls",
        )
        self.max_task_tool_calls = _limit(
            defaults["max_task_tool_calls"] if max_task_tool_calls is None else max_task_tool_calls,
            "max_task_tool_calls",
        )
        self.max_task_tokens = _limit(
            defaults["max_task_tokens"] if max_task_tokens is None else max_task_tokens,
            "max_task_tokens",
        )
        self._model_calls = 0
        self._tool_calls = 0
        self._reported_input_tokens = 0
        self._reported_output_tokens = 0
        self._reported_total_tokens = 0
        self._model_calls_with_reported_total = 0
        self._estimated_tokens = 0
        self._accounted_tokens = 0
        self._reserved_tokens = 0
        self._model_calls_with_reported_usage = 0
        self._finalized_model_calls: set[int] = set()
        self._token_reservations: dict[int, int] = {}
        self._lock = Lock()

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "TaskBudgetLedger":
        values = runtime_limit_values(config)
        return cls(
            max_model_calls=values["max_model_calls"],
            max_task_tool_calls=values["max_task_tool_calls"],
            max_task_tokens=values["max_task_tokens"],
        )

    @property
    def calls(self) -> int:
        return self.snapshot().model_calls

    def consume(self) -> int:
        return self.reserve_model_call()

    def reset(self) -> None:
        with self._lock:
            self._model_calls = 0
            self._tool_calls = 0
            self._reported_input_tokens = 0
            self._reported_output_tokens = 0
            self._reported_total_tokens = 0
            self._model_calls_with_reported_total = 0
            self._estimated_tokens = 0
            self._accounted_tokens = 0
            self._reserved_tokens = 0
            self._model_calls_with_reported_usage = 0
            self._finalized_model_calls.clear()
            self._token_reservations.clear()

    def restore_snapshot(self, snapshot: BudgetSnapshot | Mapping[str, Any]) -> None:
        values = (
            snapshot.to_dict()
            if isinstance(snapshot, BudgetSnapshot)
            else dict(snapshot)
            if isinstance(snapshot, Mapping)
            else None
        )
        if values is None:
            raise ValueError("budget checkpoint must be a mapping")

        def non_negative(name: str) -> int:
            value = values.get(name, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"invalid budget checkpoint field: {name}")
            return value

        model_calls = non_negative("model_calls")
        tool_calls = non_negative("tool_calls")
        reported_input = non_negative("reported_input_tokens")
        reported_output = non_negative("reported_output_tokens")
        reported_total = non_negative("reported_total_tokens")
        reported_total_calls = non_negative("model_calls_with_reported_total")
        estimated = non_negative("estimated_tokens")
        accounted = non_negative("accounted_tokens")
        reserved = non_negative("reserved_tokens")
        reported_calls = non_negative("model_calls_with_reported_usage")
        if model_calls > self.max_model_calls:
            raise ValueError("budget checkpoint exceeds max_model_calls")
        if tool_calls > self.max_task_tool_calls:
            raise ValueError("budget checkpoint exceeds max_task_tool_calls")
        if reported_calls > model_calls:
            raise ValueError("budget checkpoint has invalid reported model-call count")
        if reported_total_calls > model_calls:
            raise ValueError("budget checkpoint has invalid reported-total count")
        if reserved:
            raise ValueError("budget checkpoint contains an active token reservation")

        with self._lock:
            self._model_calls = model_calls
            self._tool_calls = tool_calls
            self._reported_input_tokens = reported_input
            self._reported_output_tokens = reported_output
            self._reported_total_tokens = reported_total
            self._model_calls_with_reported_total = reported_total_calls
            self._estimated_tokens = estimated
            self._accounted_tokens = accounted
            self._reserved_tokens = reserved
            self._model_calls_with_reported_usage = reported_calls
            self._finalized_model_calls = set(range(1, model_calls + 1))
            self._token_reservations.clear()

    def reserve_model_call(self, token_allowance: int = 0) -> int:
        if isinstance(token_allowance, bool) or not isinstance(token_allowance, int):
            raise TypeError("token_allowance must be an integer")
        if token_allowance < 0:
            raise ValueError("token_allowance must be non-negative")
        with self._lock:
            committed = self._accounted_tokens + self._reserved_tokens
            token_limit_reached = (
                committed >= self.max_task_tokens
                if token_allowance == 0
                else committed + token_allowance > self.max_task_tokens
            )
            if token_limit_reached:
                raise BudgetExhausted(
                    "task_tokens", self.max_task_tokens, committed
                )
            if self._model_calls >= self.max_model_calls:
                raise BudgetExhausted("model_calls", self.max_model_calls, self._model_calls)
            self._model_calls += 1
            if token_allowance:
                self._reserved_tokens += token_allowance
                self._token_reservations[self._model_calls] = token_allowance
            return self._model_calls

    def reserve_tool_call(self) -> int:
        with self._lock:
            committed = self._accounted_tokens + self._reserved_tokens
            if committed >= self.max_task_tokens:
                raise BudgetExhausted(
                    "task_tokens", self.max_task_tokens, committed
                )
            if self._tool_calls >= self.max_task_tool_calls:
                raise BudgetExhausted("tool_calls", self.max_task_tool_calls, self._tool_calls)
            self._tool_calls += 1
            return self._tool_calls

    def reservation_for(self, call_number: int) -> int:
        with self._lock:
            return self._token_reservations.get(call_number, 0)

    def finalize_model_call(
        self,
        call_number: int,
        *,
        usage: Any = None,
        estimated_tokens: int = 0,
    ) -> None:
        if isinstance(estimated_tokens, bool) or not isinstance(estimated_tokens, int):
            raise TypeError("estimated_tokens must be an integer")
        if estimated_tokens < 0:
            raise ValueError("estimated_tokens must be non-negative")
        input_tokens, output_tokens, total_tokens, normalized_total, complete = normalize_usage(usage)
        with self._lock:
            if call_number < 1 or call_number > self._model_calls:
                raise ValueError("unknown model call reservation")
            if call_number in self._finalized_model_calls:
                raise ValueError("model call reservation already finalized")
            reservation = self._token_reservations.pop(call_number, 0)
            self._reserved_tokens -= reservation
            self._finalized_model_calls.add(call_number)
            if input_tokens is not None:
                self._reported_input_tokens += input_tokens
            if output_tokens is not None:
                self._reported_output_tokens += output_tokens
            if total_tokens is not None:
                self._reported_total_tokens += total_tokens
                self._model_calls_with_reported_total += 1
            if complete:
                assert normalized_total is not None
                self._accounted_tokens += normalized_total
                self._model_calls_with_reported_usage += 1
            else:
                self._estimated_tokens += estimated_tokens
                self._accounted_tokens += estimated_tokens

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            without_usage = self._model_calls - self._model_calls_with_reported_usage
            return BudgetSnapshot(
                model_calls=self._model_calls,
                tool_calls=self._tool_calls,
                reported_input_tokens=self._reported_input_tokens,
                reported_output_tokens=self._reported_output_tokens,
                reported_total_tokens=self._reported_total_tokens,
                model_calls_with_reported_total=self._model_calls_with_reported_total,
                estimated_tokens=self._estimated_tokens,
                accounted_tokens=self._accounted_tokens,
                reserved_tokens=self._reserved_tokens,
                model_calls_with_reported_usage=self._model_calls_with_reported_usage,
                model_calls_without_reported_usage=without_usage,
                token_usage_complete=without_usage == 0,
                max_model_calls=self.max_model_calls,
                max_task_tool_calls=self.max_task_tool_calls,
                max_task_tokens=self.max_task_tokens,
            )

    @property
    def remaining_model_calls(self) -> int:
        return max(0, self.max_model_calls - self.snapshot().model_calls)

    @property
    def remaining_tool_calls(self) -> int:
        return max(0, self.max_task_tool_calls - self.snapshot().tool_calls)

    @property
    def remaining_task_tokens(self) -> int:
        snapshot = self.snapshot()
        return max(0, snapshot.max_task_tokens - snapshot.accounted_tokens - snapshot.reserved_tokens)


def task_budget_for(owner: Any, config: Mapping[str, Any]) -> TaskBudgetLedger:
    existing = getattr(owner, "task_budget", None)
    if isinstance(existing, TaskBudgetLedger):
        return existing
    ledger = TaskBudgetLedger.from_config(config)
    owner.task_budget = ledger
    return ledger


__all__ = [
    "BudgetExhausted",
    "BudgetSnapshot",
    "TaskBudgetLedger",
    "estimate_model_request_allowance",
    "estimate_payload_allowance",
    "estimate_model_request_tokens",
    "estimate_payload_tokens",
    "normalize_usage",
    "task_budget_for",
]
