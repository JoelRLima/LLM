"""Finite conversation use cases over the application's existing Agent session.

Task execution owns composition; binding resolves its original session privately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, cast

from llm_agent.agent.llm.contracts import ModelGateway as _ModelGateway
from llm_agent.agent.llm.session import ChatSession as _ChatSession
from llm_agent.agent.runtime.worker_output import emit_worker_output as _emit_worker_output
from llm_agent.application.model_errors import _translate_model_error
from llm_agent.application.task_execution import TaskExecutionRuntime


class ConversationRuntime:
    """Opaque handle; lifecycle and transcript remain with the original owner."""

    __slots__ = ("_session",)

    def __init__(self, session: object) -> None:
        self._session = cast(_ChatSession, session)


@dataclass(frozen=True)
class ConversationView:
    effective_system_prompt: str
    thinking_budget: int
    model: str
    provider: str


@dataclass(frozen=True)
class ChatRequestPreview:
    model: str
    temperature: float
    max_output_tokens: int
    stream: bool
    structured_output_mode: str | None
    message_count: int


@dataclass(frozen=True)
class HistoryOutcome:
    success: bool
    message: str


class ChatTurn:
    """One legacy turn on the same runtime; no separate session or request."""

    __slots__ = ("_conversation", "_presentation_fallback", "preview")

    def __init__(
        self,
        conversation: ConversationRuntime,
        preview: ChatRequestPreview | None,
        presentation_fallback: Callable[[object, str], None] | None = None,
    ) -> None:
        self._conversation = conversation
        self.preview = preview
        self._presentation_fallback = presentation_fallback

    def present_stream_text(self, value: object = "", *, end: str = "\n") -> None:
        _emit_worker_output(value, end=end, fallback=self._presentation_fallback)


def bind_conversation(runtime: TaskExecutionRuntime) -> ConversationRuntime:
    return ConversationRuntime(runtime._owner.session)


def read_conversation(conversation: ConversationRuntime) -> ConversationView:
    session = conversation._session
    return ConversationView(
        session.get_effective_system_prompt(), session.thinking_budget,
        session.model_profile.model, session.model_profile.provider,
    )


def configure_conversation(
    conversation: ConversationRuntime, *, system_prompt: str | None = None,
    thinking_budget: int | None = None,
) -> None:
    if system_prompt is not None:
        conversation._session.set_system_prompt(system_prompt)
    if thinking_budget is not None:
        conversation._session.thinking_budget = thinking_budget


def execute_history_command(
    conversation: ConversationRuntime, action: Literal["clear", "save", "load"],
    path: str = "chat_history.json",
) -> HistoryOutcome | None:
    session = conversation._session
    if action == "clear":
        session.clear_history()
        return None
    if action == "save":
        success, message = session.save_to_file(path)
    elif action == "load":
        success, message = session.load_from_file(path)
    else:
        raise ValueError(f"Unsupported history action: {action}")
    return HistoryOutcome(success, message)


def begin_chat_turn(
    conversation: ConversationRuntime,
    text: str,
    *,
    include_preview: bool = False,
    presentation_fallback: Callable[[object, str], None] | None = None,
) -> ChatTurn:
    session = conversation._session
    session.add_user_message(text)
    preview = None
    if include_preview:
        try:
            request = session.build_request(stream=True)
        except Exception as exc:
            translated = _translate_model_error(exc)
            if translated is exc:
                raise
            raise translated from exc
        preview = ChatRequestPreview(
            request.model, request.temperature, request.max_output_tokens, request.stream,
            request.structured_output.mode.value if request.structured_output is not None else None,
            len(request.messages),
        )
    return ChatTurn(conversation, preview, presentation_fallback)


def stream_chat_turn(turn: ChatTurn, callbacks: dict[str, Callable[..., Any]]) -> str | None:
    session = turn._conversation._session
    try:
        request = session.build_request(stream=True)
        result = session.consume_stream_request(request, callbacks)
        return str(result) if result is not None else None
    except Exception as exc:
        translated = _translate_model_error(exc)
        if translated is exc:
            raise
        raise translated from exc


def finish_chat_turn(
    turn: ChatTurn, response: str | None = None, *, failed: bool = False,
    interrupted: bool = False,
) -> None:
    session = turn._conversation._session
    if failed:
        session.remove_last_user_message()
    elif response and not interrupted:
        session.add_assistant_message(response)


def append_legacy_transcript(
    conversation: ConversationRuntime, answer: str, *, user_text: str | None = None,
) -> None:
    if user_text is not None:
        conversation._session.add_user_message(user_text)
    conversation._session.add_assistant_message(answer)


def _resolve_gateway(conversation: ConversationRuntime) -> _ModelGateway:
    return conversation._session.gateway


__all__ = [
    "ConversationRuntime", "ConversationView", "ChatRequestPreview", "HistoryOutcome", "ChatTurn",
    "bind_conversation", "read_conversation", "configure_conversation", "execute_history_command",
    "begin_chat_turn", "stream_chat_turn", "finish_chat_turn", "append_legacy_transcript",
]
