"""Authored canonical error-code registry builder."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def build_error_registry(
    error_definition_type: Any,
    failure_layer: Any,
    operational_status: Any,
) -> dict[str, Any]:
    def definitions(
        codes: Iterable[str],
        *,
        layer: Any = None,
        public_safe: bool = True,
        hard: bool = False,
        default_status: str | None = None,
        retryable: bool = False,
    ) -> tuple[Any, ...]:
        return tuple(
            error_definition_type(
                code,
                layer=layer or failure_layer.RUNTIME,
                public_safe=public_safe,
                hard=hard,
                default_status=default_status,
                retryable=retryable,
            )
            for code in codes
        )

    # This is the only authored error-code registry. Public and hard sets are
    # projections of the returned definitions.
    authored = (
        *definitions(
            ("MODEL_PROVIDER_ERROR", "PROVIDER_FAILED"),
            layer=failure_layer.PROVIDER,
            hard=True,
            default_status=operational_status.FAILED.value,
            retryable=True,
        ),
        *definitions(
            (
                "APPLICATION_AUTHORITY_DENIED", "APPLICATION_AUTHORITY_MISSING", "APPROVAL_DENIED",
                "APPROVAL_FAILED", "APPROVAL_REQUIRED", "AUTHORITY_REQUIRED", "AUTH_DENIED",
                "AUTH_REQUIRED", "DENIED", "INVALID_ARGUMENTS", "MISSING_REQUIRED_INPUT",
                "OPERATIONAL_MODE_DENIED", "ORIGIN_MISMATCH", "PERMISSION_DENIED", "REGISTRY_UNBOUND",
                "REQUEST_INVALID", "RUNTIME_MISMATCH", "TASK_AUTHORITY_DENIED", "TASK_AUTHORITY_MISSING",
                "WORKSPACE_GRANT_DENIED",
            ),
            layer=failure_layer.GATEWAY,
            hard=True,
            default_status=operational_status.PERMISSION_DENIED.value,
        ),
        *definitions(
            ("ADAPTER_FAILED", "EXECUTION_ERROR", "TOOL_ERROR", "TOOL_NOT_FOUND"),
            layer=failure_layer.TOOL,
            default_status=operational_status.FAILED.value,
            retryable=True,
        ),
        *definitions(
            ("DUPLICATE_INVOCATION_ID", "INVALID_RESPONSE", "INVALID_RESULT", "INVALID_STATUS", "INVOCATION_ID_MISMATCH"),
            layer=failure_layer.GATEWAY,
            hard=True,
            default_status=operational_status.PROTOCOL_ERROR.value,
        ),
        *definitions(
            (
                "CHECKPOINT_CORRUPT", "CHECKPOINT_INCOMPATIBLE_SCHEMA", "CHECKPOINT_INVALID",
                "CHECKPOINT_INVALID_TERMINAL_DISPOSITION", "CHECKPOINT_MIGRATION_AMBIGUOUS",
                "CHECKPOINT_TERMINAL_EVIDENCE_MISSING", "EXECUTION_ABORTED", "HIERARCHICAL_EXECUTION_FAILED",
                "TASK_CLEANUP_FAILURE",
            ),
            hard=True,
            default_status=operational_status.FAILED.value,
        ),
        *definitions(
            ("TASK_BUDGET_EXHAUSTED", "TASK_COST_LIMIT_REACHED"),
            hard=True,
            default_status=operational_status.BLOCKED.value,
        ),
        *definitions(
            ("CANCELLED",),
            hard=True,
            default_status=operational_status.CANCELLED.value,
        ),
        *definitions(
            ("MUTATING_CANCELLATION_UNSUPPORTED",),
            hard=True,
            default_status=operational_status.BLOCKED.value,
        ),
        *definitions(
            ("CANONICAL_COMMIT_FAILED", "CANONICAL_COMMIT_RETRY_BLOCKED"),
            hard=True,
            default_status=operational_status.UNVERIFIED.value,
        ),
        *definitions(
            ("UNRESOLVED_SYMBOLIC_ARGUMENT", "prepared_invocation_stale", "reasoning_boundary_blocked"),
            hard=True,
            default_status=operational_status.BLOCKED.value,
        ),
        *definitions(
            ("SAFETY_BLOCK",),
            public_safe=False,
            hard=True,
            default_status=operational_status.BLOCKED.value,
        ),
        *definitions(
            ("WATCHDOG_NO_PROGRESS", "WATCHDOG_REPEATED_FAILURE"),
            hard=True,
            default_status=operational_status.FAILED.value,
        ),
        *definitions(
            ("WATCHDOG_TIMEOUT", "TIMEOUT"),
            hard=True,
            default_status=operational_status.TIMED_OUT.value,
        ),
        *definitions(
            (
                "ROUTE_FALLBACK_REASON_MISSING", "ROUTE_HANDLED_ANSWER_MISSING", "ROUTE_RESULT_CONTRACT_VIOLATION",
                "ROUTE_TERMINAL_TRUTH_INVALID", "ROUTE_TERMINAL_TRUTH_MISSING",
            ),
            hard=True,
            default_status=operational_status.PROTOCOL_ERROR.value,
        ),
        *definitions(
            (
                "SECURITY_ANALYZER_BLOCKED", "SECURITY_ANALYZER_CANCELLED", "SECURITY_ANALYZER_DENIED",
                "SECURITY_ANALYZER_FAILED", "SECURITY_ANALYZER_PROTOCOL_ERROR", "SECURITY_ANALYZER_TIMED_OUT",
                "SECURITY_ANALYZER_UNAVAILABLE", "SECURITY_ANALYZER_UNVERIFIED", "SECURITY_GATEWAY_UNAVAILABLE",
                "SECURITY_TARGET_UNAVAILABLE",
            ),
            default_status=operational_status.UNVERIFIED.value,
            retryable=True,
        ),
        *definitions(
            (
                "completion_review_failed", "obligation_evidence_missing", "prohibited_effect_occurred",
                "requested_effect_pending", "task_obligation_blocked", "task_obligation_pending",
                "unrequested_effect_occurred", "unresolved_symbolic_argument",
            ),
            hard=True,
            default_status=operational_status.BLOCKED.value,
        ),
        *definitions(
            ("AUTHORITY_DENIED",),
            public_safe=True,
            default_status=operational_status.PERMISSION_DENIED.value,
        ),
        *definitions(
            ("TOOL_BLOCKED",),
            public_safe=True,
            default_status=operational_status.BLOCKED.value,
        ),
        *definitions(
            ("TOOL_UNAVAILABLE",),
            public_safe=True,
            default_status=operational_status.UNAVAILABLE.value,
        ),
    )
    registry: dict[str, Any] = {}
    for definition in authored:
        if definition.code in registry:
            raise RuntimeError(f"duplicate canonical error code: {definition.code}")
        registry[definition.code] = definition
    return registry


__all__ = ["build_error_registry"]
