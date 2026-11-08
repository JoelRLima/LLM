"""Primitive presentation contracts; execution retains outcome authority.

The headless documents preserve the two existing serializers. Interactive
observations expose only presentation facts, never Agent owners or evidence.
"""

from __future__ import annotations

from typing import Literal, TypeAlias, TypedDict

PrimitiveScalar: TypeAlias = bool | int | float | str | None
PrimitiveValue: TypeAlias = (
    PrimitiveScalar | list["PrimitiveValue"] | tuple["PrimitiveValue", ...] | dict[str, "PrimitiveValue"]
)
PrimitiveDocument: TypeAlias = dict[str, PrimitiveValue]


class RunObservation(TypedDict):
    status: str
    success: bool
    answer: str
    workspace: str
    error: str | None
    diagnostics: list[PrimitiveDocument]
    metadata: PrimitiveDocument
    receipt: PrimitiveDocument
    report_path: str | None
    snapshot: PrimitiveDocument | None


class InteractionRunObservation(TypedDict):
    status: str
    success: bool
    answer: str
    error: str | None


class InteractionObservation(TypedDict):
    status: str
    success: bool
    answer: str
    resolution: PrimitiveDocument | None
    run_result: InteractionRunObservation | None
    error: str | None
    reason_code: str | None
    interaction_usage: PrimitiveDocument


class ResolutionObservation(TypedDict):
    action: str
    boundary: str
    provenance: str
    ambiguity: str
    directive: str | None
    deliberation_profile: str | None
    reason_code: str | None


class _InteractiveDetails(TypedDict, total=False):
    error: str
    reason_code: str
    interaction_usage: PrimitiveDocument
    resolution: ResolutionObservation
    receipt: PrimitiveDocument
    report_path: str | None
    run_id: str
    report_completeness: Literal["final"]
    model_transport_failure: Literal["MODEL_CONNECTION_ERROR", "MODEL_TIMEOUT"]


class InteractiveTaskObservation(_InteractiveDetails):
    status: str
    success: bool
    answer: str
    legacy_transcript: bool


HeadlessTaskObservation: TypeAlias = RunObservation | InteractionObservation
TaskObservation: TypeAlias = HeadlessTaskObservation | InteractiveTaskObservation

__all__ = [
    "PrimitiveScalar", "PrimitiveValue", "PrimitiveDocument", "RunObservation",
    "InteractionRunObservation", "InteractionObservation", "ResolutionObservation",
    "InteractiveTaskObservation", "HeadlessTaskObservation", "TaskObservation",
]
