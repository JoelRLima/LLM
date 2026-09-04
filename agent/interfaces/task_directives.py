"""Side-effect-free parsing for task-boundary W11 directive prefixes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agent.runtime.task_directives import (
    MAX_STRING_LENGTH,
    DeliberationProfile,
    TaskDirective,
    TaskRunDirective,
)

TASK_DIRECTIVE_CONFLICT = "TASK_DIRECTIVE_CONFLICT"
TASK_PROFILE_CONFLICT = "TASK_PROFILE_CONFLICT"
TASK_DIRECTIVE_UNKNOWN_PREFIX_TOKEN = "TASK_DIRECTIVE_UNKNOWN_PREFIX_TOKEN"
TASK_DIRECTIVE_OBJECTIVE_REQUIRED = "TASK_DIRECTIVE_OBJECTIVE_REQUIRED"
TASK_CONTINUE_ARGUMENTS_NOT_ALLOWED = "TASK_CONTINUE_ARGUMENTS_NOT_ALLOWED"
TASK_DIRECTIVE_OBJECTIVE_TOO_LONG = "TASK_DIRECTIVE_OBJECTIVE_TOO_LONG"

_DIRECTIVE_TOKENS = {
    "/read": TaskDirective.READ,
    "/plan": TaskDirective.PLAN,
    "/do": TaskDirective.DO,
}
_PROFILE_TOKENS = {
    "/economy": DeliberationProfile.ECONOMY,
    "/normal": DeliberationProfile.NORMAL,
    "/smart": DeliberationProfile.SMART,
    "/cautious": DeliberationProfile.CAUTIOUS,
}
_ALL_PREFIX_TOKENS = frozenset((*_DIRECTIVE_TOKENS, *_PROFILE_TOKENS, "/continue"))


class TaskRequestAction(str, Enum):
    RUN = "run"
    CONTINUE = "continue"


TaskEntryAction = TaskRequestAction


class TaskDirectiveParseError(ValueError):
    """Stable public parse failure without exposing implementation details."""

    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        self.reason_code = reason_code
        self.code = reason_code
        self.detail = detail or reason_code
        super().__init__(self.detail)


@dataclass(frozen=True, slots=True)
class ParsedTaskRequest:
    action: TaskRequestAction
    directive: TaskRunDirective | None

    def __post_init__(self) -> None:
        if not isinstance(self.action, TaskRequestAction):
            object.__setattr__(self, "action", TaskRequestAction(self.action))
        if self.action is TaskRequestAction.RUN and not isinstance(self.directive, TaskRunDirective):
            raise ValueError("RUN requires a TaskRunDirective")
        if self.action is TaskRequestAction.CONTINUE and self.directive is not None:
            raise ValueError("CONTINUE cannot carry a TaskRunDirective")

    @classmethod
    def run(cls, directive: TaskRunDirective) -> "ParsedTaskRequest":
        return cls(TaskRequestAction.RUN, directive)

    @classmethod
    def continue_(cls) -> "ParsedTaskRequest":
        return cls(TaskRequestAction.CONTINUE, None)

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


def parse_task_request(raw: str) -> ParsedTaskRequest:
    """Parse only leading W11 slash tokens at a task execution boundary."""

    if type(raw) is not str:
        raise TaskDirectiveParseError(TASK_DIRECTIVE_OBJECTIVE_REQUIRED)
    text = raw.strip()
    if not text:
        raise TaskDirectiveParseError(TASK_DIRECTIVE_OBJECTIVE_REQUIRED)

    tokens = tuple(_token_spans(text))
    first_token = tokens[0][0].casefold()
    if first_token not in _ALL_PREFIX_TOKENS:
        return ParsedTaskRequest.run(_build_directive(TaskDirective.AUTO, DeliberationProfile.NORMAL, text))

    directive = TaskDirective.AUTO
    profile = DeliberationProfile.NORMAL
    seen_directive = False
    seen_profile = False
    index = 0

    while index < len(tokens) and tokens[index][0].startswith("/"):
        token, _, _ = tokens[index]
        normalized = token.casefold()

        if normalized == "/continue":
            if seen_directive or seen_profile:
                raise TaskDirectiveParseError(TASK_DIRECTIVE_CONFLICT)
            if index != len(tokens) - 1:
                raise TaskDirectiveParseError(TASK_CONTINUE_ARGUMENTS_NOT_ALLOWED)
            return ParsedTaskRequest.continue_()

        if normalized in _DIRECTIVE_TOKENS:
            if seen_directive:
                raise TaskDirectiveParseError(TASK_DIRECTIVE_CONFLICT)
            directive = _DIRECTIVE_TOKENS[normalized]
            seen_directive = True
        elif normalized in _PROFILE_TOKENS:
            if seen_profile:
                raise TaskDirectiveParseError(TASK_PROFILE_CONFLICT)
            profile = _PROFILE_TOKENS[normalized]
            seen_profile = True
        else:
            raise TaskDirectiveParseError(TASK_DIRECTIVE_UNKNOWN_PREFIX_TOKEN)
        index += 1

    if index == len(tokens):
        raise TaskDirectiveParseError(TASK_DIRECTIVE_OBJECTIVE_REQUIRED)

    subject = text[tokens[index][1] :]
    return ParsedTaskRequest.run(_build_directive(directive, profile, subject))


def _build_directive(
    directive: TaskDirective,
    profile: DeliberationProfile,
    subject: str,
) -> TaskRunDirective:
    if not subject.strip():
        raise TaskDirectiveParseError(TASK_DIRECTIVE_OBJECTIVE_REQUIRED)
    if len(subject) > MAX_STRING_LENGTH:
        raise TaskDirectiveParseError(TASK_DIRECTIVE_OBJECTIVE_TOO_LONG)
    if directive is TaskDirective.PLAN:
        proposal_length = len(
            "Propose a validated execution plan for the following objective; "
            "do not apply or execute the proposed changes. Subject: "
            + subject
        )
        if proposal_length > MAX_STRING_LENGTH:
            raise TaskDirectiveParseError(TASK_DIRECTIVE_OBJECTIVE_TOO_LONG)
    try:
        return TaskRunDirective(directive=directive, deliberation_profile=profile, subject=subject)
    except ValueError as exc:
        reason = str(exc)
        if reason == TASK_DIRECTIVE_OBJECTIVE_TOO_LONG:
            raise TaskDirectiveParseError(TASK_DIRECTIVE_OBJECTIVE_TOO_LONG) from exc
        if reason == TASK_DIRECTIVE_OBJECTIVE_REQUIRED:
            raise TaskDirectiveParseError(TASK_DIRECTIVE_OBJECTIVE_REQUIRED) from exc
        raise


def _token_spans(text: str) -> list[tuple[str, int, int]]:
    result: list[tuple[str, int, int]] = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        start = index
        while index < len(text) and not text[index].isspace():
            index += 1
        result.append((text[start:index], start, index))
    return result


__all__ = [
    "ParsedTaskRequest",
    "TASK_CONTINUE_ARGUMENTS_NOT_ALLOWED",
    "TASK_DIRECTIVE_CONFLICT",
    "TASK_DIRECTIVE_OBJECTIVE_REQUIRED",
    "TASK_DIRECTIVE_OBJECTIVE_TOO_LONG",
    "TASK_DIRECTIVE_UNKNOWN_PREFIX_TOKEN",
    "TASK_PROFILE_CONFLICT",
    "TaskDirectiveParseError",
    "TaskEntryAction",
    "TaskRequestAction",
    "parse_task_request",
]
