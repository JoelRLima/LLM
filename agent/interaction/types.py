"""Frozen public and internal value objects for W12 interaction admission."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from agent.runtime.task_directives import DeliberationProfile, TaskDirective

if TYPE_CHECKING:
    from agent.application_result import AgentRunResult


class InteractionBoundary(str, Enum):
    NATURAL = "natural"
    TASK = "task"


class InteractionAction(str, Enum):
    RESPOND = "respond"
    CLARIFY = "clarify"
    RUN = "run"
    CONTINUE = "continue"


class InteractionProvenance(str, Enum):
    EXPLICIT = "explicit"
    MODEL_INFERRED = "model_inferred"
    DETERMINISTIC = "deterministic"


class InteractionAmbiguity(str, Enum):
    NONE = "none"
    EFFECT = "effect"
    CONTINUATION = "continuation"
    GROUNDING = "grounding"
    CONFLICT = "conflict"


class ActionGrounding(str, Enum):
    NONE = "none"
    CURRENT_TURN = "current_turn"
    CONTEXTUAL = "contextual"


def _enum(value: Any, kind: type[Enum], name: str) -> Any:
    if isinstance(value, kind):
        return value
    try:
        return kind(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} is invalid") from exc


@dataclass(frozen=True, slots=True)
class InteractionModelDecision:
    """The exact eight-field advisory contract returned by the resolver."""

    action: InteractionAction
    directive: TaskDirective | None
    ambiguity: InteractionAmbiguity
    grounding: ActionGrounding
    operation_requested: bool
    proposal_only: bool
    resume_requested: bool
    evidence: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _enum(self.action, InteractionAction, "action"))
        if self.directive is not None:
            directive = _enum(self.directive, TaskDirective, "directive")
            if directive is TaskDirective.AUTO:
                raise ValueError("AUTO is not valid in the interaction contract")
            object.__setattr__(self, "directive", directive)
        object.__setattr__(self, "ambiguity", _enum(self.ambiguity, InteractionAmbiguity, "ambiguity"))
        object.__setattr__(self, "grounding", _enum(self.grounding, ActionGrounding, "grounding"))
        for name in ("operation_requested", "proposal_only", "resume_requested"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if type(self.evidence) is not str:
            raise ValueError("evidence must be a string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "directive": self.directive.value if self.directive is not None else "none",
            "ambiguity": self.ambiguity.value,
            "grounding": self.grounding.value,
            "operation_requested": self.operation_requested,
            "proposal_only": self.proposal_only,
            "resume_requested": self.resume_requested,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class InteractionResolution:
    action: InteractionAction
    boundary: InteractionBoundary
    directive: TaskDirective | None
    deliberation_profile: DeliberationProfile | None
    provenance: InteractionProvenance
    ambiguity: InteractionAmbiguity
    subject: str | None
    reason_code: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _enum(self.action, InteractionAction, "action"))
        object.__setattr__(self, "boundary", _enum(self.boundary, InteractionBoundary, "boundary"))
        if self.directive is not None:
            directive = _enum(self.directive, TaskDirective, "directive")
            if directive is TaskDirective.AUTO:
                raise ValueError("AUTO is not valid in an admitted fresh interaction")
            object.__setattr__(self, "directive", directive)
        if self.deliberation_profile is not None:
            object.__setattr__(
                self,
                "deliberation_profile",
                _enum(self.deliberation_profile, DeliberationProfile, "deliberation_profile"),
            )
        object.__setattr__(self, "provenance", _enum(self.provenance, InteractionProvenance, "provenance"))
        object.__setattr__(self, "ambiguity", _enum(self.ambiguity, InteractionAmbiguity, "ambiguity"))
        if self.subject is not None and type(self.subject) is not str:
            raise ValueError("subject must be a string or None")
        if self.reason_code is not None and type(self.reason_code) is not str:
            raise ValueError("reason_code must be a string or None")

    def to_dict(self) -> dict[str, Any]:
        """Bounded public projection; advisory evidence is deliberately omitted."""

        return {
            "action": self.action.value,
            "boundary": self.boundary.value,
            "directive": self.directive.value if self.directive is not None else None,
            "deliberation_profile": (
                self.deliberation_profile.value if self.deliberation_profile is not None else None
            ),
            "provenance": self.provenance.value,
            "ambiguity": self.ambiguity.value,
            "subject": self.subject,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class AgentInteractionResult:
    status: str
    answer: str
    resolution: InteractionResolution | None
    run_result: "AgentRunResult | None" = None
    error: str | None = None
    reason_code: str | None = None
    interaction_usage: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.status) is not str or not self.status:
            raise ValueError("status must be a non-empty string")
        if type(self.answer) is not str:
            raise ValueError("answer must be a string")
        if self.error is not None and type(self.error) is not str:
            raise ValueError("error must be a string or None")
        if self.reason_code is not None and type(self.reason_code) is not str:
            raise ValueError("reason_code must be a string or None")
        if not isinstance(self.interaction_usage, Mapping):
            raise ValueError("interaction_usage must be a mapping")

    @property
    def success(self) -> bool:
        if self.run_result is not None:
            return bool(getattr(self.run_result, "success", False))
        return self.status == "succeeded"

    @property
    def ok(self) -> bool:
        return self.success

    def to_dict(self) -> dict[str, Any]:
        usage: dict[str, Any] = {}
        for key in ("model_calls", "accounted_tokens", "token_usage_complete"):
            value = self.interaction_usage.get(key)
            if type(value) in (int, bool):
                usage[key] = value
        resolution = self.resolution.to_dict() if self.resolution is not None else None
        run_projection: Any = None
        if self.run_result is not None:
            run_projection = {
                "status": str(getattr(self.run_result, "status", "failed")),
                "success": bool(getattr(self.run_result, "success", False)),
                "answer": str(getattr(self.run_result, "answer", "")),
                "error": (
                    getattr(self.run_result, "error", None)
                    if isinstance(getattr(self.run_result, "error", None), str)
                    else None
                ),
            }
        return {
            "success": self.success,
            "status": self.status,
            "answer": self.answer,
            "resolution": resolution,
            "run_result": run_projection,
            "error": self.error,
            "reason_code": self.reason_code,
            "interaction_usage": usage,
        }


__all__ = [
    "ActionGrounding",
    "AgentInteractionResult",
    "InteractionAction",
    "InteractionAmbiguity",
    "InteractionBoundary",
    "InteractionModelDecision",
    "InteractionProvenance",
    "InteractionResolution",
]
