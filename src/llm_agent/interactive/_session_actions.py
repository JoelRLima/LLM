"""Session local controls that do not require a terminal presentation."""

from __future__ import annotations

from concurrent.futures import Future
from typing import TYPE_CHECKING, Any

from llm_agent.interactive.admission import build_agentic_envelope, build_natural_envelope
from llm_agent.interactive.commands import DEFAULT_COMMAND_REGISTRY
from llm_agent.interactive.session_controls import CONTROL_ACTION_IDS, attention_snapshot

if TYPE_CHECKING:
    from llm_agent.interactive.session import InteractiveSession


def execution_mode(session: InteractiveSession) -> str:
    from llm_agent.application.task_execution import read_execution_capabilities

    return read_execution_capabilities(session._runtime).label if session._runtime is not None else "READ_ONLY"


def command_catalog(_session: InteractiveSession) -> list[dict[str, Any]]:
    return [
        {
            "action_id": binding.action_id,
            "command": " ".join(binding.preferred_path),
            "title": definition.title,
            "description": definition.description,
            "payload_hint": definition.payload_hint,
            "busy_policy": binding.busy_policy,
            "model_use": binding.model_use,
            "aliases": [" ".join(alias) for alias in binding.aliases],
            "local_control": binding.action_id in CONTROL_ACTION_IDS - {"run.pending_send"} or binding.action_id in {"session.exit", "process.exec"},
        }
        for definition in DEFAULT_COMMAND_REGISTRY.catalog
        for binding in [DEFAULT_COMMAND_REGISTRY.binding_for_action(definition.action_id)]
    ]


def attention(session: InteractiveSession) -> dict[str, Any] | None:
    return attention_snapshot(session)


def resolve_attention(
    session: InteractiveSession,
    attention_id: int,
    decision: str,
    run_generation: int,
    request_fingerprint: str,
) -> str:
    if decision not in {"approve", "deny"}:
        raise ValueError("invalid approval decision")
    return session.approval_broker.resolve(
        attention_id,
        decision,
        run_generation=run_generation,
        request_fingerprint=request_fingerprint,
    )


def enqueue(session: InteractiveSession, text: str) -> dict[str, Any]:
    from llm_agent.interactive.action_parser import parse_action

    if not isinstance(text, str) or not text.strip() or len(text) > session._max_input_chars:
        raise ValueError("INPUT_INVALID")
    match = parse_action(text)
    if (match is None and text.lstrip().startswith("/")) or (
        match is not None and match.binding.routing_kind != "AGENTIC"
    ):
        raise ValueError("Only follow-ups and agentic commands can be queued")
    with session._lock:
        if session._closed or not session.controller.is_busy():
            raise ValueError("No active task; submit the message directly")
        envelope = build_agentic_envelope(text, match) if match else build_natural_envelope(text)
        item = session.controller.pending.add(envelope)
        return {
            "status": "succeeded",
            "answer": f"Mensagem pendente #{item.pending_id}; envie com /pending send {item.pending_id} após a tarefa encerrar.",
            "pending_id": item.pending_id,
        }


def send_pending(session: InteractiveSession, pending_id: int) -> Future[Any]:
    with session._lock:
        session._check_idle()
        item = session.controller.pending.inspect(pending_id)
        if item is None:
            raise ValueError("pending item not found")
        future = session.submit(item.visible_text)
        session.controller.pending.consume(pending_id)
        return future


__all__ = [
    "attention",
    "command_catalog",
    "enqueue",
    "execution_mode",
    "resolve_attention",
    "send_pending",
]
