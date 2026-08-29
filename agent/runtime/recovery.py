"""Closed recovery scopes, immutable policy, and task-owned consumption state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from threading import Lock
from typing import Any


class RecoveryScope(str, Enum):
    """The bounded recovery mechanisms provided by the runtime."""

    STRUCTURED_RESPONSE_REPAIRS = "structured_response_repairs"
    SEMANTIC_SELECTION_REPAIRS = "semantic_selection_repairs"
    VALIDATION_REPAIRS = "validation_repairs"
    HEURISTIC_REPLANS = "heuristic_replans"
    LLM_REPLANS = "llm_replans"
    EFFECT_CONTINUATIONS = "effect_continuations"
    REASONING_CONTINUATIONS = "reasoning_continuations"


RECOVERY_SCOPES = tuple(RecoveryScope)
_REPLAN_SCOPES = frozenset({RecoveryScope.HEURISTIC_REPLANS, RecoveryScope.LLM_REPLANS})
_DEFAULT_LIMITS = {
    RecoveryScope.STRUCTURED_RESPONSE_REPAIRS: 1, RecoveryScope.SEMANTIC_SELECTION_REPAIRS: 1,
    RecoveryScope.VALIDATION_REPAIRS: 1, RecoveryScope.HEURISTIC_REPLANS: 2,
    RecoveryScope.LLM_REPLANS: 1, RecoveryScope.EFFECT_CONTINUATIONS: 1,
    RecoveryScope.REASONING_CONTINUATIONS: 1,
}
_CANONICAL_REPLAN_CAP = 2


def _scope(value: RecoveryScope | str) -> RecoveryScope:
    if isinstance(value, RecoveryScope):
        return value
    if not isinstance(value, str):
        raise TypeError("recovery scope must be a RecoveryScope or stable string")
    try:
        return RecoveryScope(value)
    except ValueError as exc:
        raise ValueError(f"unknown recovery scope: {value}") from exc


def _non_negative(value: Any, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(message)
    return value


def _normalized_usage(used: Mapping[str, Any], label: str) -> dict[RecoveryScope, int]:
    normalized = {item: 0 for item in RECOVERY_SCOPES}
    for raw_scope, raw_value in used.items():
        try:
            scope = _scope(raw_scope)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} contains an unknown scope") from exc
        normalized[scope] = _non_negative(raw_value, f"{label} counter is invalid")
    return normalized


def _validate_usage(policy: "RecoveryPolicy", used: Mapping[RecoveryScope, int]) -> None:
    for scope in RECOVERY_SCOPES:
        if used[scope] > policy.limit(scope):
            raise ValueError("recovery budget counter exceeds its policy limit")
    total = used[RecoveryScope.HEURISTIC_REPLANS] + used[RecoveryScope.LLM_REPLANS]
    if total > policy.aggregate_replan_cap:
        raise ValueError("recovery budget exceeds its aggregate replan limit")


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    """Immutable per-scope recovery limits and the canonical replan aggregate."""

    limits: Mapping[RecoveryScope, int]

    def __post_init__(self) -> None:
        normalized: dict[RecoveryScope, int] = {}
        if not isinstance(self.limits, Mapping):
            raise TypeError("recovery policy limits must be a mapping")
        for raw_scope, raw_limit in self.limits.items():
            scope = _scope(raw_scope)
            normalized[scope] = _non_negative(
                raw_limit, "recovery policy limit must be non-negative"
            )
        if set(normalized) != set(RECOVERY_SCOPES):
            message = (
                "recovery policy is missing a canonical scope"
                if set(RECOVERY_SCOPES) - set(normalized)
                else "recovery policy contains an unknown scope"
            )
            raise ValueError(message)
        from types import MappingProxyType

        object.__setattr__(self, "limits", MappingProxyType(normalized))

    @property
    def aggregate_replan_cap(self) -> int:
        """Return the fixed aggregate cap shared by heuristic and LLM replans."""

        return _CANONICAL_REPLAN_CAP

    @classmethod
    def default(cls) -> "RecoveryPolicy":
        return cls.from_config(None)

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None = None) -> "RecoveryPolicy":
        limits = dict(_DEFAULT_LIMITS)
        from agent.runtime.limits import runtime_limit_values

        limits[RecoveryScope.REASONING_CONTINUATIONS] = runtime_limit_values(config)[
            "max_reasoning_turns"
        ]
        return cls(limits)

    def limit(self, scope: RecoveryScope | str) -> int:
        return self.limits[_scope(scope)]

    def to_dict(self) -> dict[str, int]:
        return {item.value: self.limits[item] for item in RECOVERY_SCOPES}


class RecoveryBudgetState:
    """Single task-owned, atomic consumption owner for recovery attempts."""

    def __init__(self, policy: RecoveryPolicy | None = None) -> None:
        self._policy = policy or RecoveryPolicy.from_config(None)
        self._used = {item: 0 for item in RECOVERY_SCOPES}
        self._lock = Lock()

    @property
    def policy(self) -> RecoveryPolicy:
        return self._policy

    def reconfigure(self, policy: RecoveryPolicy) -> None:
        if not isinstance(policy, RecoveryPolicy):
            raise TypeError("recovery policy must be a RecoveryPolicy")
        with self._lock:
            _validate_usage(policy, self._used)
            self._policy = policy

    def limit(self, scope: RecoveryScope | str) -> int:
        return self._policy.limit(scope)

    def used(self, scope: RecoveryScope | str) -> int:
        normalized = _scope(scope)
        with self._lock:
            return self._used[normalized]

    def _aggregate_remaining(self) -> int:
        used = self._used[RecoveryScope.HEURISTIC_REPLANS] + self._used[RecoveryScope.LLM_REPLANS]
        return max(0, self._policy.aggregate_replan_cap - used)

    def remaining(self, scope: RecoveryScope | str) -> int:
        normalized = _scope(scope)
        with self._lock:
            remaining = max(0, self._policy.limit(normalized) - self._used[normalized])
            aggregate = self._aggregate_remaining() if normalized in _REPLAN_SCOPES else remaining
            return min(remaining, aggregate)

    def _fits(self, scope: RecoveryScope, amount: int) -> bool:
        if self._used[scope] + amount > self._policy.limit(scope):
            return False
        return scope not in _REPLAN_SCOPES or self._aggregate_remaining() >= amount

    def can_attempt(self, scope: RecoveryScope | str, amount: int = 1) -> bool:
        normalized = _scope(scope)
        amount = _non_negative(amount, "recovery attempt amount must be non-negative")
        with self._lock:
            return self._fits(normalized, amount)

    def try_consume(self, scope: RecoveryScope | str, amount: int = 1) -> bool:
        """Atomically authorize and consume one bounded recovery attempt."""

        normalized = _scope(scope)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise ValueError("recovery attempt amount must be a positive integer")
        with self._lock:
            if not self._fits(normalized, amount):
                return False
            self._used[normalized] += amount
            return True

    def set_projection_used(self, scope: RecoveryScope | str, value: int) -> None:
        """Set a legacy compatibility projection after validating its shape."""

        normalized = _scope(scope)
        value = _non_negative(value, "recovery counter must be non-negative")
        with self._lock:
            candidate = dict(self._used)
            candidate[normalized] = value
            _validate_usage(self._policy, candidate)
            self._used[normalized] = value

    def reset(self) -> None:
        with self._lock:
            self._used = {item: 0 for item in RECOVERY_SCOPES}

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {item.value: self._used[item] for item in RECOVERY_SCOPES}

    def remaining_snapshot(self) -> dict[str, int]:
        with self._lock:
            aggregate = self._aggregate_remaining()
            return {
                item.value: min(
                    max(0, self._policy.limit(item) - self._used[item]), aggregate
                )
                if item in _REPLAN_SCOPES
                else max(0, self._policy.limit(item) - self._used[item])
                for item in RECOVERY_SCOPES
            }

    def to_dict(self) -> dict[str, dict[str, int]]:
        return {
            "limits": self._policy.to_dict(),
            "used": self.snapshot(),
            "remaining": self.remaining_snapshot(),
        }

    def to_checkpoint_dict(self) -> dict[str, dict[str, int]]:
        return {"used": self.snapshot()}

    def restore_snapshot(self, raw: Mapping[str, Any]) -> None:
        if not isinstance(raw, Mapping):
            raise ValueError("recovery budget snapshot must be an object")
        used = raw.get("used") if "used" in raw else raw
        if not isinstance(used, Mapping):
            raise ValueError("recovery budget used state must be an object")
        normalized = _normalized_usage(used, "recovery budget")
        with self._lock:
            _validate_usage(self._policy, normalized)
            self._used = normalized

    def restore_legacy_projection(
        self,
        *,
        continuation_attempts: int,
        replan_counts: Mapping[str, Any],
        reasoning_turns_used: int,
    ) -> None:
        continuation = _non_negative(
            continuation_attempts, "legacy continuation counter is invalid"
        )
        reasoning = _non_negative(
            reasoning_turns_used, "legacy reasoning counter is invalid"
        )
        if not isinstance(replan_counts, Mapping):
            raise ValueError("legacy replan counters are invalid")
        heuristic = _non_negative(
            replan_counts.get("heuristic", 0), "legacy replan counters are invalid"
        )
        llm = _non_negative(
            replan_counts.get("llm", 0), "legacy replan counters are invalid"
        )
        total = _non_negative(
            replan_counts.get("total", 0), "legacy replan counters are invalid"
        )
        if total < heuristic + llm:
            raise ValueError("legacy replan total is inconsistent")
        if total != heuristic + llm:
            raise ValueError("legacy replan total contains a surplus attempt")
        values = {
            RecoveryScope.EFFECT_CONTINUATIONS: continuation,
            RecoveryScope.HEURISTIC_REPLANS: heuristic,
            RecoveryScope.LLM_REPLANS: llm,
            RecoveryScope.REASONING_CONTINUATIONS: reasoning,
        }
        with self._lock:
            normalized = {item: values.get(item, 0) for item in RECOVERY_SCOPES}
            _validate_usage(self._policy, normalized)
            self._used = normalized

    def __getstate__(self) -> dict[str, Any]:
        return {
            "limits": self._policy.to_dict(),
            "used": self.snapshot(),
        }

    def __setstate__(self, state: Mapping[str, Any]) -> None:
        self._policy = RecoveryPolicy(state["limits"])
        self._used = {item: 0 for item in RECOVERY_SCOPES}
        self._lock = Lock()
        self.restore_snapshot({"used": state["used"]})


__all__ = [
    "RECOVERY_SCOPES",
    "RecoveryBudgetState",
    "RecoveryPolicy",
    "RecoveryScope",
]
