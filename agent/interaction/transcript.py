"""Canonical W12 transcript validation, bounded views, and commit helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from agent.application_result import AgentRunResult

MAX_PRIOR_MESSAGE_LENGTH = 1024
MAX_PRIOR_PAIRS = 4
MAX_PRIOR_CONTENT = 4096


def validate_transcript_messages(messages: Any) -> None:
    if type(messages) is not list or not messages:
        raise ValueError("transcript must be a non-empty list")
    first = messages[0]
    if type(first) is not dict or first.get("role") != "system" or type(first.get("content")) is not str:
        raise ValueError("transcript must begin with a system message")
    for message in messages:
        if type(message) is not dict:
            raise ValueError("transcript message must be an object")
        if type(message.get("role")) is not str or type(message.get("content")) is not str:
            raise ValueError("transcript role and content must be strings")


validate_transcript = validate_transcript_messages
validate_message_history = validate_transcript_messages


def snapshot_visible_messages(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    validate_transcript_messages(list(messages))
    return deepcopy([dict(message) for message in messages])


def restore_visible_messages(session: Any, snapshot: Sequence[Mapping[str, Any]]) -> None:
    session.messages = deepcopy([dict(message) for message in snapshot])


def bounded_prior_pairs(messages: Sequence[Mapping[str, Any]]) -> list[tuple[dict[str, str], dict[str, str]]]:
    """Return only whole, adjacent, completed user/assistant pairs."""

    validate_transcript_messages(list(messages))
    accepted: list[tuple[dict[str, str], dict[str, str]]] = []
    index = 1
    while index + 1 < len(messages):
        user = messages[index]
        assistant = messages[index + 1]
        if (
            user.get("role") == "user"
            and assistant.get("role") == "assistant"
            and len(user["content"]) <= MAX_PRIOR_MESSAGE_LENGTH
            and len(assistant["content"]) <= MAX_PRIOR_MESSAGE_LENGTH
        ):
            accepted.append(
                (
                    {"role": "user", "content": user["content"]},
                    {"role": "assistant", "content": assistant["content"]},
                )
            )
            index += 2
        else:
            index += 1
    accepted = accepted[-MAX_PRIOR_PAIRS:]
    while accepted and sum(len(item["content"]) for pair in accepted for item in pair) > MAX_PRIOR_CONTENT:
        accepted.pop(0)
    return accepted


def provider_message_projection(
    messages: Sequence[Mapping[str, Any]],
    *,
    current_user: str | None = None,
    include_prior: bool = True,
) -> list[dict[str, str]]:
    """Project canonical storage to the provider's closed three-role view."""

    validate_transcript_messages(list(messages))
    result = [{"role": "system", "content": messages[0]["content"]}]
    if include_prior:
        for user, assistant in bounded_prior_pairs(messages):
            result.extend((user, assistant))
    if current_user is not None:
        result.append({"role": "user", "content": current_user})
    return result


def resolver_prior_messages(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for user, assistant in bounded_prior_pairs(messages):
        result.extend((user, assistant))
    return result


def commit_one_pair(session: Any, visible_user_text: str, answer: str) -> None:
    if type(visible_user_text) is not str or type(answer) is not str:
        raise ValueError("visible transcript pair must contain strings")
    session.messages.append({"role": "user", "content": visible_user_text})
    session.messages.append({"role": "assistant", "content": answer})


def visible_text_for_run_result(result: AgentRunResult) -> str:
    answer = getattr(result, "answer", None)
    if isinstance(answer, str) and answer.strip():
        return answer
    error = getattr(result, "error", None)
    if isinstance(error, str) and error.strip():
        return error
    status = str(getattr(result, "status", "failed"))
    return {
        "succeeded": "A tarefa foi concluída.",
        "needs_input": "A tarefa precisa de mais informações.",
        "unavailable": "A tarefa está indisponível no momento.",
        "failed": "A tarefa falhou.",
    }.get(status, "A tarefa terminou sem uma resposta estável.")


__all__ = [
    "MAX_PRIOR_CONTENT",
    "MAX_PRIOR_MESSAGE_LENGTH",
    "MAX_PRIOR_PAIRS",
    "bounded_prior_pairs",
    "commit_one_pair",
    "provider_message_projection",
    "resolver_prior_messages",
    "restore_visible_messages",
    "snapshot_visible_messages",
    "validate_message_history",
    "validate_transcript",
    "validate_transcript_messages",
    "visible_text_for_run_result",
]
