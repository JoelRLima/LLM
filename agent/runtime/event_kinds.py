"""Closed taxonomy for canonical runtime events."""

from __future__ import annotations

from enum import Enum


class RuntimeEventKind(str, Enum):
    """The complete runtime envelope taxonomy used by production emitters."""

    APPROVAL_APPROVED = "approval_approved"
    APPROVAL_REQUESTED = "approval_requested"
    CACHE_HIT = "cache_hit"
    CANONICAL_REVIEW_AMENDMENT = "canonical_review_amendment"
    CANONICAL_REVIEW_REJECTED = "canonical_review_rejected"
    CANONICAL_REVIEW_REQUIRED_REJECTION = "canonical_review_required_rejection"
    CHECKPOINT_DEFERRED = "checkpoint_deferred"
    CHECKPOINT_PERSISTENCE_FAILED = "checkpoint_persistence_failed"
    CODE_ANALYSIS_COMPLETED = "code_analysis_completed"
    CODE_ANALYSIS_STARTED = "code_analysis_started"
    CODE_CONTEXT_SELECTED = "code_context_selected"
    CONTINUATION_PLAN_PROPOSED = "continuation_plan_proposed"
    COST_LIMIT = "cost_limit"
    DEFERRED_CONDITION_BLOCKED = "deferred_condition_blocked"
    DEFERRED_CONDITION_RESOLVED = "deferred_condition_resolved"
    DIRECT_RESPONSE = "direct_response"
    EFFECT_WAIVER_BOUND = "effect_waiver_bound"
    ERROR = "error"
    FINAL = "final"
    HARD_BLOCK = "hard_block"
    HIERARCHICAL_COMPLETED = "hierarchical_completed"
    HIERARCHICAL_FALLBACK = "hierarchical_fallback"
    HIERARCHICAL_STARTED = "hierarchical_started"
    LEGACY_STARTED = "started"
    MODEL_CALL_COMPLETED = "model_call_completed"
    MODEL_CALL_STARTED = "model_call_started"
    PLAN_CREATED = "plan_created"
    PLAN_EXTENDED = "plan_extended"
    REASONING_BOUNDARY_PLAN_PROPOSED = "reasoning_boundary_plan_proposed"
    REPLAN = "replan"
    REPLAN_BLOCKED = "replan_blocked"
    ROUTE_TRANSITION = "route_transition"
    STEP_BLOCKED = "step_blocked"
    STEP_CANCELLED = "step_cancelled"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    STEP_SKIPPED = "step_skipped"
    STEP_UNVERIFIED = "step_unverified"
    TASK_BLOCKED = "task_blocked"
    TASK_NODE_STARTED = "task_node_started"
    TASK_OUTCOME = "task_outcome"
    TASK_POLICY_DECISION = "task_policy_decision"
    TOOL_DISCOVERY = "tool_discovery"
    TOOL_DENIED = "tool_denied"
    TOOL_END = "tool_end"
    TOOL_START = "tool_start"
    VALIDATION_REPAIR = "validation_repair"
    WARNING = "warning"
    WATCHDOG = "watchdog"

    @classmethod
    def coerce(cls, value: "RuntimeEventKind | str") -> "RuntimeEventKind":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                return cls(value)
            except ValueError as exc:
                raise ValueError(f"unsupported runtime event kind: {value!r}") from exc
        raise TypeError("runtime event kind must be a RuntimeEventKind or string")


__all__ = ["RuntimeEventKind"]
