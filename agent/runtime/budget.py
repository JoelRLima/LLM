from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from threading import Lock
from typing import Any


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
    estimated_tokens: int
    accounted_tokens: int
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

def _token_value(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value

def _usage_value(usage: Any, primary: str, legacy: str) -> int | None:
    if usage is None:
        return None
    if isinstance(usage, Mapping):
        value = usage.get(primary, usage.get(legacy))
    else:
        value = getattr(usage, primary, None)
        if value is None:
            value = getattr(usage, legacy, None)
    return _token_value(value)

def _usage_available(usage: Any) -> bool:
    if isinstance(usage, Mapping):
        return usage.get("available", True) is not False
    return getattr(usage, "available", True) is not False

def _extract_usage(usage: Any) -> tuple[int | None, int | None, int | None]:
    if usage is None or not _usage_available(usage):
        return None, None, None
    return (
        _usage_value(usage, "input_tokens", "prompt_tokens"),
        _usage_value(usage, "output_tokens", "completion_tokens"),
        _usage_value(usage, "total_tokens", "total_tokens"),
    )

def estimate_payload_tokens(payload: Any, response: Any = None) -> int:
    texts: list[str] = []
    messages = payload.get("messages") if isinstance(payload, Mapping) else None
    if isinstance(messages, list):
        texts.extend(
            str(message.get("content", ""))
            for message in messages
            if isinstance(message, Mapping)
        )
    if isinstance(response, str):
        texts.append(response)
    elif isinstance(response, Mapping):
        content = response.get("content")
        if isinstance(content, str):
            texts.append(content)
    else:
        content = getattr(response, "content", None)
        if isinstance(content, str):
            texts.append(content)
    return sum(len(text) for text in texts) // 4


def estimate_model_request_tokens(request: Any, response: Any = None) -> int:
    messages = getattr(request, "messages", ())
    texts = [str(getattr(message, "content", "")) for message in messages]
    content = getattr(response, "content", None)
    if isinstance(content, str):
        texts.append(content)
    return sum(len(text) for text in texts) // 4


class TaskBudgetLedger:
    def __init__(
        self,
        max_model_calls: int = 20,
        max_task_tool_calls: int = 60,
        max_task_tokens: int = 200_000,
    ) -> None:
        self.max_model_calls = _limit(max_model_calls, "max_model_calls")
        self.max_task_tool_calls = _limit(max_task_tool_calls, "max_task_tool_calls")
        self.max_task_tokens = _limit(max_task_tokens, "max_task_tokens")
        self._model_calls = 0
        self._tool_calls = 0
        self._reported_input_tokens = 0
        self._reported_output_tokens = 0
        self._reported_total_tokens = 0
        self._estimated_tokens = 0
        self._accounted_tokens = 0
        self._model_calls_with_reported_usage = 0
        self._finalized_model_calls: set[int] = set()
        self._lock = Lock()

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "TaskBudgetLedger":
        return cls(max_model_calls=int(config.get("max_model_calls", 20)), max_task_tool_calls=int(config.get("max_task_tool_calls", 60)), max_task_tokens=int(config.get("max_task_tokens", 200_000)))

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
            self._estimated_tokens = 0
            self._accounted_tokens = 0
            self._model_calls_with_reported_usage = 0
            self._finalized_model_calls.clear()

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
        estimated = non_negative("estimated_tokens")
        accounted = non_negative("accounted_tokens")
        reported_calls = non_negative("model_calls_with_reported_usage")
        if model_calls > self.max_model_calls:
            raise ValueError("budget checkpoint exceeds max_model_calls")
        if tool_calls > self.max_task_tool_calls:
            raise ValueError("budget checkpoint exceeds max_task_tool_calls")
        if reported_calls > model_calls:
            raise ValueError("budget checkpoint has invalid reported model-call count")

        with self._lock:
            self._model_calls = model_calls
            self._tool_calls = tool_calls
            self._reported_input_tokens = reported_input
            self._reported_output_tokens = reported_output
            self._reported_total_tokens = reported_total
            self._estimated_tokens = estimated
            self._accounted_tokens = accounted
            self._model_calls_with_reported_usage = reported_calls
            self._finalized_model_calls = set(range(1, model_calls + 1))

    def reserve_model_call(self) -> int:
        with self._lock:
            if self._accounted_tokens >= self.max_task_tokens:
                raise BudgetExhausted(
                    "task_tokens", self.max_task_tokens, self._accounted_tokens
                )
            if self._model_calls >= self.max_model_calls:
                raise BudgetExhausted("model_calls", self.max_model_calls, self._model_calls)
            self._model_calls += 1
            return self._model_calls

    def reserve_tool_call(self) -> int:
        with self._lock:
            if self._accounted_tokens >= self.max_task_tokens:
                raise BudgetExhausted(
                    "task_tokens", self.max_task_tokens, self._accounted_tokens
                )
            if self._tool_calls >= self.max_task_tool_calls:
                raise BudgetExhausted("tool_calls", self.max_task_tool_calls, self._tool_calls)
            self._tool_calls += 1
            return self._tool_calls

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
        input_tokens, output_tokens, total_tokens = _extract_usage(usage)
        complete = input_tokens is not None and output_tokens is not None
        with self._lock:
            if call_number < 1 or call_number > self._model_calls:
                raise ValueError("unknown model call reservation")
            if call_number in self._finalized_model_calls:
                raise ValueError("model call reservation already finalized")
            self._finalized_model_calls.add(call_number)
            if input_tokens is not None:
                self._reported_input_tokens += input_tokens
            if output_tokens is not None:
                self._reported_output_tokens += output_tokens
            if total_tokens is not None:
                self._reported_total_tokens += total_tokens
            if complete:
                assert input_tokens is not None
                assert output_tokens is not None
                normalized_total = (
                    total_tokens
                    if total_tokens is not None
                    else input_tokens + output_tokens
                )
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
                estimated_tokens=self._estimated_tokens,
                accounted_tokens=self._accounted_tokens,
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
    "estimate_model_request_tokens",
    "estimate_payload_tokens",
    "task_budget_for",
]
