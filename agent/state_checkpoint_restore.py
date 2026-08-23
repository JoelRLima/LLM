"""Transactional helpers for restoring an AgentState checkpoint."""

from __future__ import annotations

import copy
from threading import Lock
from typing import Any


def provisional_state(state: Any) -> Any:
    """Create an isolated restore target without mutating the live state."""

    try:
        provisional = copy.copy(state)
        for name, value in state.__dict__.items():
            if name not in {"memory", "budget_ledger"}:
                setattr(provisional, name, copy.deepcopy(value))
        memory = getattr(state, "memory", None)
        if memory is not None:
            staged_memory = copy.copy(memory)
            if hasattr(memory, "state"):
                staged_memory.state = copy.deepcopy(memory.state)
            provisional.memory = staged_memory
        provisional.budget_ledger = _clone_budget_ledger(
            getattr(state, "budget_ledger", None)
        )
        return provisional
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Checkpoint restore could not create provisional state.") from exc


def _clone_budget_ledger(budget: Any) -> Any:
    if budget is None:
        return None
    staged = copy.copy(budget)
    values = getattr(budget, "__dict__", None)
    if not isinstance(values, dict):
        raise ValueError("Checkpoint restore could not stage budget state.")
    staged.__dict__.clear()
    staged.__dict__.update(
        {
            name: copy.deepcopy(value)
            for name, value in values.items()
            if name != "_lock"
        }
    )
    if "_lock" in values:
        staged._lock = Lock()
    return staged


def validate_restored_cross_fields(state: Any) -> None:
    if getattr(state, "terminal_disposition", None) != "complete":
        return
    pending_effects = tuple(getattr(state, "pending_effects", lambda: ())())
    pending_obligations = tuple(getattr(state, "pending_obligations", lambda: ())())
    blocked_obligations = tuple(getattr(state, "blocked_obligations", lambda: ())())
    prohibited_effects = tuple(
        getattr(state, "prohibited_effects_occurred", lambda: ())()
    )
    evidence_complete = bool(
        getattr(state, "terminal_evidence_complete", lambda: False)()
    )
    if (
        pending_effects
        or pending_obligations
        or blocked_obligations
        or prohibited_effects
        or not evidence_complete
    ):
        raise ValueError("Checkpoint complete disposition conflicts with pending semantics.")


def publish_provisional_state(target: Any, provisional: Any) -> None:
    original_memory = getattr(target, "memory", None)
    original_budget = getattr(target, "budget_ledger", None)
    original_attributes = dict(target.__dict__)
    has_original_memory_state = original_memory is not None and hasattr(original_memory, "state")
    if has_original_memory_state:
        assert original_memory is not None
        original_memory_state = copy.deepcopy(original_memory.state)
    else:
        original_memory_state = None
    original_budget_snapshot = (
        original_budget.snapshot()
        if original_budget is not None and callable(getattr(original_budget, "snapshot", None))
        else None
    )
    try:
        for name, value in provisional.__dict__.items():
            if name not in {"memory", "budget_ledger"}:
                setattr(target, name, value)
        staged_memory = getattr(provisional, "memory", None)
        if original_memory is not None and staged_memory is not None and hasattr(staged_memory, "state"):
            original_memory.state = copy.deepcopy(staged_memory.state)
            target.memory = original_memory
        else:
            target.memory = staged_memory
        staged_budget = getattr(provisional, "budget_ledger", None)
        if original_budget is not None and staged_budget is not None:
            original_budget.restore_snapshot(staged_budget.snapshot())
            target.budget_ledger = original_budget
        else:
            target.budget_ledger = staged_budget
    except BaseException:
        target.__dict__.clear()
        target.__dict__.update(original_attributes)
        if original_memory is not None and has_original_memory_state:
            original_memory.state = original_memory_state
        if original_budget is not None and original_budget_snapshot is not None:
            original_budget.restore_snapshot(original_budget_snapshot)
        raise
