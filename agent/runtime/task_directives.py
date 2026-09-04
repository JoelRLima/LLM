"""Typed, task-scoped directive and deliberation values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, ClassVar

from agent.task_definition.model_validation import MAX_STRING_LENGTH

TASK_RUN_DIRECTIVE_SCHEMA_VERSION = 1
PLAN_OBJECTIVE_PREFIX = (
    "Propose a validated execution plan for the following objective; "
    "do not apply or execute the proposed changes. Subject: "
)
READ_CAPABILITY_CEILING = frozenset({"read", "vcs_read", "analyze"})
ABSENT = object()


class TaskDirective(str, Enum):
    """Closed vocabulary for the task's requested execution posture."""

    AUTO = "auto"
    READ = "read"
    PLAN = "plan"
    DO = "do"


class DeliberationProfile(str, Enum):
    """Closed vocabulary for task-local reasoning policy."""

    ECONOMY = "economy"
    NORMAL = "normal"
    SMART = "smart"
    CAUTIOUS = "cautious"

    def effective_reasoning_budget(self, baseline: int) -> int:
        if self is DeliberationProfile.ECONOMY:
            return 0
        if self is DeliberationProfile.SMART:
            return max(baseline, 1024)
        if self is DeliberationProfile.CAUTIOUS:
            return max(baseline, 2048)
        return baseline

    def hierarchical_allowed(self) -> bool:
        return self is not DeliberationProfile.ECONOMY

    def trivial_shortcut_allowed(self) -> bool:
        return self is not DeliberationProfile.CAUTIOUS


@dataclass(frozen=True, slots=True)
class TaskRunDirective:
    """Immutable W11 task directive retained across a task's lifetime."""

    directive: TaskDirective
    deliberation_profile: DeliberationProfile
    subject: str

    _CHECKPOINT_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"schema_version", "directive", "deliberation_profile", "subject"}
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "directive", _coerce_enum(self.directive, TaskDirective, "directive"))
        object.__setattr__(
            self,
            "deliberation_profile",
            _coerce_enum(self.deliberation_profile, DeliberationProfile, "deliberation_profile"),
        )
        if type(self.subject) is not str:
            raise ValueError("subject must be a string")
        if not self.subject.strip():
            raise ValueError("TASK_DIRECTIVE_OBJECTIVE_REQUIRED")
        if len(self.subject) > MAX_STRING_LENGTH:
            raise ValueError("TASK_DIRECTIVE_OBJECTIVE_TOO_LONG")
        if len(self.canonical_objective()) > MAX_STRING_LENGTH:
            raise ValueError("TASK_DIRECTIVE_OBJECTIVE_TOO_LONG")

    def canonical_objective(self) -> str:
        """Return the one canonical objective sent through task-definition gates."""

        if self.directive is not TaskDirective.PLAN:
            return self.subject
        return PLAN_OBJECTIVE_PREFIX + self.subject

    def effective_reasoning_budget(self, baseline: int) -> int:
        return self.deliberation_profile.effective_reasoning_budget(baseline)

    def hierarchical_allowed(self) -> bool:
        return self.deliberation_profile.hierarchical_allowed()

    def trivial_shortcut_allowed(self) -> bool:
        return self.directive is not TaskDirective.PLAN and self.deliberation_profile.trivial_shortcut_allowed()

    def capability_ceiling(self) -> frozenset[str] | None:
        if self.directive is TaskDirective.READ:
            return READ_CAPABILITY_CEILING
        return None

    def to_checkpoint_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TASK_RUN_DIRECTIVE_SCHEMA_VERSION,
            "directive": self.directive.value,
            "deliberation_profile": self.deliberation_profile.value,
            "subject": self.subject,
        }

    @classmethod
    def from_checkpoint_dict(cls, value: Any) -> "TaskRunDirective":
        if not isinstance(value, Mapping):
            raise ValueError("task_run_directive must be an object")
        if set(value) != cls._CHECKPOINT_FIELDS:
            raise ValueError("task_run_directive contains unknown or missing fields")
        schema_version = value["schema_version"]
        if type(schema_version) is not int or schema_version != TASK_RUN_DIRECTIVE_SCHEMA_VERSION:
            raise ValueError("task_run_directive schema_version is invalid")
        directive = _checkpoint_enum(value["directive"], TaskDirective, "directive")
        profile = _checkpoint_enum(value["deliberation_profile"], DeliberationProfile, "deliberation_profile")
        subject = value["subject"]
        if type(subject) is not str:
            raise ValueError("subject must be a string")
        return cls(directive=directive, deliberation_profile=profile, subject=subject)

    def to_dict(self) -> dict[str, Any]:
        return self.to_checkpoint_dict()

    @classmethod
    def from_dict(cls, value: Any) -> "TaskRunDirective":
        return cls.from_checkpoint_dict(value)


def validate_checkpoint_task_run_directive(
    *,
    objective: str,
    raw: object = ABSENT,
    plan_present: bool,
    terminal_disposition: str | None,
    materialize: bool = True,
) -> TaskRunDirective | None:
    """Validate the W11 relation embedded in one checkpoint.

    This pure helper is shared by execution-state restore and the model-free
    continuity classifier so both boundaries agree about W11 compatibility.
    """

    if type(objective) is not str or not objective.strip():
        raise ValueError("checkpoint objective must be a non-empty string")
    if type(plan_present) is not bool:
        raise ValueError("checkpoint plan presence is invalid")
    if terminal_disposition is not None and type(terminal_disposition) is not str:
        raise ValueError("checkpoint terminal disposition is invalid")
    directive: TaskRunDirective | None = None
    if raw is ABSENT:
        if materialize:
            directive = TaskRunDirective(
                TaskDirective.AUTO,
                DeliberationProfile.NORMAL,
                objective,
            )
    else:
        directive = TaskRunDirective.from_checkpoint_dict(raw)
    if directive is not None and directive.canonical_objective() != objective:
        raise ValueError("checkpoint task_run_directive does not match its objective")
    if (
        directive is not None
        and
        directive.directive is TaskDirective.PLAN
        and terminal_disposition is None
        and plan_present
    ):
        raise ValueError("checkpoint PLAN directive cannot resume with an executable plan")
    return directive


def _coerce_enum(value: Any, enum_type: type[Enum], field: str) -> Any:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is invalid") from exc


def _checkpoint_enum(value: Any, enum_type: type[Enum], field: str) -> Any:
    if type(value) is not str:
        raise ValueError(f"{field} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"{field} is invalid") from exc


__all__ = [
    "DeliberationProfile",
    "ABSENT",
    "MAX_STRING_LENGTH",
    "PLAN_OBJECTIVE_PREFIX",
    "READ_CAPABILITY_CEILING",
    "TASK_RUN_DIRECTIVE_SCHEMA_VERSION",
    "TaskDirective",
    "TaskRunDirective",
    "validate_checkpoint_task_run_directive",
]
