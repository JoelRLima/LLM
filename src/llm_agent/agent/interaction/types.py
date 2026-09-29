"""Frozen public and internal value objects for W12 interaction admission."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from llm_agent.agent.runtime.task_directives import (
    DeliberationProfile,
    TaskDirective,
    TaskRunDirective,
)

if TYPE_CHECKING:
    from llm_agent.agent.application_result import AgentRunResult
    from llm_agent.agent.planning.intent_admission import AuthorityEnvelope


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


class TaskRequestAction(str, Enum):
    """Action selected by parsing a task-boundary request."""

    RUN = "run"
    CONTINUE = "continue"


TaskEntryAction = TaskRequestAction


@dataclass(frozen=True, slots=True)
class ParsedTaskRequest:
    """Interaction-owned parse result, carrying the admitted runtime directive."""

    action: TaskRequestAction
    directive: TaskRunDirective | None
    directive_explicit: bool = False
    profile_explicit: bool = False
    prefix_recognized: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.action, TaskRequestAction):
            object.__setattr__(self, "action", TaskRequestAction(self.action))
        if self.action is TaskRequestAction.RUN and not isinstance(self.directive, TaskRunDirective):
            raise ValueError("RUN requires a TaskRunDirective")
        if self.action is TaskRequestAction.CONTINUE and self.directive is not None:
            raise ValueError("CONTINUE cannot carry a TaskRunDirective")

    @classmethod
    def run(
        cls,
        directive: TaskRunDirective,
        *,
        directive_explicit: bool = False,
        profile_explicit: bool = False,
        prefix_recognized: bool = False,
    ) -> "ParsedTaskRequest":
        return cls(
            TaskRequestAction.RUN,
            directive,
            directive_explicit,
            profile_explicit,
            prefix_recognized,
        )

    @classmethod
    def run_from_values(
        cls,
        directive: str,
        deliberation_profile: str,
        subject: str,
        *,
        directive_explicit: bool = False,
        profile_explicit: bool = False,
        prefix_recognized: bool = False,
    ) -> "ParsedTaskRequest":
        """Materialize the runtime directive carried by this interaction DTO."""

        runtime_directive = TaskRunDirective(
            directive=TaskDirective(directive),
            deliberation_profile=DeliberationProfile(deliberation_profile),
            subject=subject,
        )
        return cls.run(
            runtime_directive,
            directive_explicit=directive_explicit,
            profile_explicit=profile_explicit,
            prefix_recognized=prefix_recognized,
        )

    @classmethod
    def continue_(cls) -> "ParsedTaskRequest":
        return cls(TaskRequestAction.CONTINUE, None, False, False, True)

    @property
    def task_run_directive(self) -> TaskRunDirective | None:
        return self.directive

    @property
    def directive_state(self) -> TaskRunDirective | None:
        """Canonical name used by task-boundary adapters."""

        return self.directive

    @property
    def subject(self) -> str | None:
        """Return the parsed subject, or no subject for CONTINUE."""

        return self.directive.subject if self.directive is not None else None


@dataclass(frozen=True, slots=True)
class AdmissionContext:
    boundary: InteractionBoundary
    visible_user_text: str
    subject: str
    parsed_task: ParsedTaskRequest | None = None
    model_decision: InteractionModelDecision | None = None
    authority_envelope: AuthorityEnvelope | None = None


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
    intent_claim: Any | None = None

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
        if self.intent_claim is not None:
            from .intent_claim import IntentClaimV1

            if not isinstance(self.intent_claim, IntentClaimV1):
                raise ValueError("intent_claim must be an IntentClaimV1")

    def to_dict(self) -> dict[str, Any]:
        from .result import _model_decision_dict

        return _model_decision_dict(self)


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
    intent_claim: Any | None = None
    admitted_intent: Any | None = None

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
        if self.intent_claim is not None:
            from .intent_claim import IntentClaimV1

            if not isinstance(self.intent_claim, IntentClaimV1):
                raise ValueError("intent_claim must be an IntentClaimV1")
        if self.admitted_intent is not None:
            from llm_agent.agent.planning.intent_admission import AdmittedIntent

            if not isinstance(self.admitted_intent, AdmittedIntent):
                raise ValueError("admitted_intent must be an AdmittedIntent")

    def to_dict(self) -> dict[str, Any]:
        """Bounded public projection; advisory evidence is deliberately omitted."""

        from .result import _interaction_resolution_dict

        return _interaction_resolution_dict(self)


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
        from .result import _validate_agent_interaction_result

        _validate_agent_interaction_result(self)

    @property
    def success(self) -> bool:
        if self.run_result is not None:
            return bool(getattr(self.run_result, "success", False))
        return self.status == "succeeded"

    @property
    def ok(self) -> bool:
        return self.success

    def to_dict(self) -> dict[str, Any]:
        from .result import _agent_interaction_result_dict

        return _agent_interaction_result_dict(self)


__all__ = [
    "ActionGrounding",
    "AdmissionContext",
    "AgentInteractionResult",
    "InteractionAction",
    "InteractionAmbiguity",
    "InteractionBoundary",
    "InteractionModelDecision",
    "InteractionProvenance",
    "InteractionResolution",
    "ParsedTaskRequest",
    "TaskEntryAction",
    "TaskRequestAction",
]
