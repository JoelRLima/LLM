"""Side-effect-free parsing for task-boundary W11 directive prefixes."""

from __future__ import annotations

from .types import ParsedTaskRequest, TaskEntryAction, TaskRequestAction

TASK_DIRECTIVE_CONFLICT = "TASK_DIRECTIVE_CONFLICT"
TASK_PROFILE_CONFLICT = "TASK_PROFILE_CONFLICT"
TASK_DIRECTIVE_UNKNOWN_PREFIX_TOKEN = "TASK_DIRECTIVE_UNKNOWN_PREFIX_TOKEN"
TASK_DIRECTIVE_OBJECTIVE_REQUIRED = "TASK_DIRECTIVE_OBJECTIVE_REQUIRED"
TASK_CONTINUE_ARGUMENTS_NOT_ALLOWED = "TASK_CONTINUE_ARGUMENTS_NOT_ALLOWED"
TASK_DIRECTIVE_OBJECTIVE_TOO_LONG = "TASK_DIRECTIVE_OBJECTIVE_TOO_LONG"

_DIRECTIVE_TOKENS = {
    "/read": "read",
    "/plan": "plan",
    "/do": "do",
}
_PROFILE_TOKENS = {
    "/economy": "economy",
    "/normal": "normal",
    "/smart": "smart",
    "/cautious": "cautious",
}
_ALL_PREFIX_TOKENS = frozenset((*_DIRECTIVE_TOKENS, *_PROFILE_TOKENS, "/continue"))


class TaskDirectiveParseError(ValueError):
    """Stable public parse failure without exposing implementation details."""

    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        self.reason_code = reason_code
        self.code = reason_code
        self.detail = detail or reason_code
        super().__init__(self.detail)


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
        return _build_request(
            "auto",
            "normal",
            text,
            directive_explicit=False,
            profile_explicit=False,
            prefix_recognized=False,
        )

    directive = "auto"
    profile = "normal"
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
            if index > 0 and _looks_like_absolute_path(token):
                break
            raise TaskDirectiveParseError(TASK_DIRECTIVE_UNKNOWN_PREFIX_TOKEN)
        index += 1

    if index == len(tokens):
        raise TaskDirectiveParseError(TASK_DIRECTIVE_OBJECTIVE_REQUIRED)

    subject = text[tokens[index][1] :]
    return _build_request(
        directive,
        profile,
        subject,
        directive_explicit=seen_directive,
        profile_explicit=seen_profile,
        prefix_recognized=True,
    )


def _build_request(
    directive: str,
    profile: str,
    subject: str,
    *,
    directive_explicit: bool,
    profile_explicit: bool,
    prefix_recognized: bool,
) -> ParsedTaskRequest:
    try:
        return ParsedTaskRequest.run_from_values(
            directive,
            profile,
            subject,
            directive_explicit=directive_explicit,
            profile_explicit=profile_explicit,
            prefix_recognized=prefix_recognized,
        )
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


def _looks_like_absolute_path(token: str) -> bool:
    return token.startswith("/") and (
        "/" in token[1:] or "\\" in token[1:] or "." in token[1:]
    )


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
