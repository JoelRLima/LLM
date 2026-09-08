"""Request-local context pressure orchestration.

The existing W13 projection/fitting owner remains responsible for selecting
bounded records.  This module owns only the W15 decision envelope and safety
geometry, so no second context-limit or fitting implementation is introduced.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from agent.llm.context_projection import (
    ContextRequestFit,
    ContextSourceRecord,
    fit_contextual_request,
)
from agent.runtime.request_measurement import RequestInputMeasurement

MIN_SAFETY_MARGIN = 512
MAX_SAFETY_MARGIN = 4096


class ContextPressureDecision(str, Enum):
    FULL = "FULL"
    COMPACT = "COMPACT"
    MANDATORY_OVERFLOW = "MANDATORY_OVERFLOW"


def context_safety_margin(context_limit: int | None) -> int:
    """Return one bounded deterministic reserve for exact request geometry."""

    if isinstance(context_limit, bool) or not isinstance(context_limit, int) or context_limit <= 0:
        return MIN_SAFETY_MARGIN
    return max(MIN_SAFETY_MARGIN, min(MAX_SAFETY_MARGIN, context_limit // 16))


@dataclass(frozen=True, slots=True)
class ContextPressureResult:
    """Bounded decision and the already-built canonical request fit."""

    fit: ContextRequestFit
    decision: ContextPressureDecision
    mandatory_measurement: RequestInputMeasurement
    final_measurement: RequestInputMeasurement | None
    safety_margin: int
    included_source_kinds: tuple[str, ...] = ()
    dropped_source_kinds: tuple[str, ...] = ()
    reason_code: str = ""

    @property
    def request(self) -> Any:
        return self.fit.request

    @property
    def projection(self) -> Any:
        return self.fit.projection

    @property
    def fit_proven(self) -> bool:
        return bool(self.fit.projection.fit_proven)

    @property
    def mandatory_overflow(self) -> bool:
        return self.decision is ContextPressureDecision.MANDATORY_OVERFLOW

    @property
    def dispatch_allowed(self) -> bool:
        return self.fit.dispatch_allowed and not self.mandatory_overflow

    def to_dict(self) -> dict[str, Any]:
        measurement = self.mandatory_measurement.to_dict()
        final = self.final_measurement.to_dict() if self.final_measurement is not None else None
        return {
            "decision": self.decision.value,
            "safety_margin": self.safety_margin,
            "fit_proven": self.fit_proven,
            "mandatory_overflow": self.mandatory_overflow,
            "dispatch_allowed": self.dispatch_allowed,
            "measurement": measurement,
            "final_measurement": final,
            "included_source_kinds": list(self.included_source_kinds),
            "dropped_source_kinds": list(self.dropped_source_kinds),
            "reason_code": self.reason_code,
        }


def _record_kinds(records: Sequence[ContextSourceRecord], *, included: bool) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            record.source_kind
            for record in records
            if bool(record.included) is included
        )
    )


def decide_context_pressure(
    *,
    mandatory_request: Any,
    required_records: Sequence[ContextSourceRecord] = (),
    optional_records: Sequence[ContextSourceRecord] = (),
    context_limit: int | None,
    gateway: Any,
    build_request: Callable[[str | None, str | None], Any],
    safety_margin: int | None = None,
) -> ContextPressureResult:
    """Decide FULL/COMPACT/mandatory overflow for one request only.

    Inexact measurement deliberately receives no optional records.  The
    canonical fitting owner then constructs the minimal deterministic compact
    request and marks the projection as unproven; callers may dispatch that
    one request, but this owner never retries merely because sizing was
    uncertain.
    """

    margin = context_safety_margin(context_limit) if safety_margin is None else safety_margin
    if isinstance(margin, bool) or not isinstance(margin, int) or margin < 0:
        raise ValueError("safety_margin must be a non-negative integer")
    known_limit = context_limit if isinstance(context_limit, int) and not isinstance(context_limit, bool) and context_limit > 0 else None

    # Without exact geometry, retaining optional history based on an estimate
    # would falsely promote it.  Required records remain in the canonical
    # request and are handled by the existing W13 fitting owner.
    initial_optional = tuple(optional_records)
    fit = fit_contextual_request(
        mandatory_request=mandatory_request,
        required_records=required_records,
        optional_records=initial_optional,
        context_limit=known_limit,
        gateway=gateway,
        build_request=build_request,
        safety_margin=margin,
    )
    measurement = fit.mandatory_measurement
    final_measurement = fit.final_measurement
    if not measurement.exact:
        # A known limit already makes the W13 owner drop optional records.  A
        # missing limit needs the same fail-safe behavior explicitly.
        if known_limit is None and initial_optional:
            fit = fit_contextual_request(
                mandatory_request=mandatory_request,
                required_records=required_records,
                optional_records=(),
                context_limit=None,
                gateway=gateway,
                build_request=build_request,
                safety_margin=margin,
            )
            measurement = fit.mandatory_measurement
            final_measurement = fit.final_measurement
        decision = ContextPressureDecision.COMPACT
        reason = "measurement_inexact"
    elif fit.mandatory_overflow:
        decision = ContextPressureDecision.MANDATORY_OVERFLOW
        reason = "mandatory_exact_overflow"
    else:
        active_optional = tuple(record for record in initial_optional if record.included)
        selected_optional = tuple(
            record
            for record in fit.projection.source_records
            if record in initial_optional and record.included
        )
        all_optional_retained = len(selected_optional) == len(active_optional) and not fit.projection.optional_truncated
        final = final_measurement or measurement
        full_geometry_safe = (
            known_limit is not None
            and final.exact
            and final.token_count is not None
            and final.token_count + int(getattr(fit.request, "max_output_tokens", 0)) + margin <= known_limit
        )
        if all_optional_retained and full_geometry_safe:
            decision = ContextPressureDecision.FULL
            reason = "exact_full_fit"
        else:
            decision = ContextPressureDecision.COMPACT
            reason = "exact_pressure"
    fit = replace(
        fit,
        decision=decision.value,
        safety_margin=margin,
        dispatch_allowed=decision is not ContextPressureDecision.MANDATORY_OVERFLOW,
    )
    records = tuple(fit.projection.source_records)
    return ContextPressureResult(
        fit=fit,
        decision=decision,
        mandatory_measurement=measurement,
        final_measurement=final_measurement,
        safety_margin=margin,
        included_source_kinds=_record_kinds(records, included=True),
        dropped_source_kinds=_record_kinds(records, included=False),
        reason_code=reason,
    )


class ContextPressureController:
    """Small injectable façade around the one request-local pressure owner."""

    def __init__(self, gateway: Any, context_limit: int | None, *, safety_margin: int | None = None) -> None:
        self.gateway = gateway
        self.context_limit = context_limit
        self.safety_margin = safety_margin

    def decide(self, **kwargs: Any) -> ContextPressureResult:
        kwargs.setdefault("gateway", self.gateway)
        kwargs.setdefault("context_limit", self.context_limit)
        kwargs.setdefault("safety_margin", self.safety_margin)
        return decide_context_pressure(**kwargs)

    def fit(self, **kwargs: Any) -> ContextPressureResult:
        return self.decide(**kwargs)


fit_context_under_pressure = decide_context_pressure


__all__ = [
    "ContextPressureController",
    "ContextPressureDecision",
    "ContextPressureResult",
    "MAX_SAFETY_MARGIN",
    "MIN_SAFETY_MARGIN",
    "context_safety_margin",
    "decide_context_pressure",
    "fit_context_under_pressure",
]
