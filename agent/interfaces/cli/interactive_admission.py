"""Single admission boundary for interactive agentic and local commands."""

from __future__ import annotations

from typing import Any

from agent.interfaces.cli import interactive_rendering
from agent.interfaces.cli.action_parser import parse_action
from agent.interfaces.cli.action_registry import CliActionMatch
from agent.interfaces.cli.commands import handle_command
from agent.interfaces.cli.interactive_worker import execute_submission
from agent.interfaces.cli.ui import console


def agentic_payload(text: str, command_id: str) -> str | None:
    match = parse_action(text)
    if command_id == "run.retry":
        return "/continue"
    if match is None or match.action_id != command_id:
        return None
    payload = match.raw_payload
    return None if command_id == "interaction.code_submit" and payload.casefold() in {"help", "ajuda"} else payload or None


def build_agentic_envelope(text: str, entry: CliActionMatch) -> Any:
    from agent.interfaces.cli.controller import SubmissionEnvelope

    payload = agentic_payload(text, entry.action_id)
    return SubmissionEnvelope(
        run_generation=0,
        visible_text=text,
        command_id=entry.action_id,
        routing_kind=entry.binding.routing_kind,
        busy_policy=entry.binding.busy_policy,
        busy_submit=entry.binding.busy_submit,
        payload=payload or "",
        boundary=entry.binding.agentic_boundary or "natural",
        owner=entry.binding.agentic_owner or entry.binding.handler_owner or "unknown",
    )


def build_natural_envelope(text: str) -> Any:
    from agent.interfaces.cli.controller import SubmissionEnvelope

    return SubmissionEnvelope(0, text, "natural_text", "AGENTIC", "AGENTIC_SUBMIT", "PENDING_EXACT_TEXT", text, "natural", "agent.application.AgentApplication.interact")


def confirm_exit(ctx: Any) -> bool:
    controller = getattr(ctx, "controller", None)
    pending = controller.pending.list() if controller is not None else ()
    if not pending and not getattr(ctx, "draft_text", ""):
        return True
    message = f"\nHá {len(pending)} pendência(s) não enviada(s). Sair e descartá-las? [s/N] "
    shell = getattr(ctx, "shell", None)
    try:
        answer = shell.prompt_line(message, default="n") if shell is not None else interactive_rendering.prompt_from(console, message, default="n", suppress_interrupt=True)
    except (EOFError, KeyboardInterrupt):
        return False
    return str(answer or "").strip().casefold() in {"s", "sim", "y", "yes"}


def _execute(ctx: Any, controller: Any, envelope: Any) -> Any:
    return controller.submit(envelope, lambda submitted, cancel_event, register: execute_submission(ctx, submitted, cancel_event, register))


def _begin_run(ctx: Any, outcome: Any, owner: str) -> None:
    view_model = getattr(ctx, "view_model", None)
    if outcome.disposition == "ACCEPTED" and view_model is not None:
        view_model.begin_run(outcome.run_generation or 0, owner=owner)


def _submit_controller(ctx: Any, controller: Any, envelope: Any) -> bool:
    outcome = _execute(ctx, controller, envelope)
    _begin_run(ctx, outcome, envelope.owner)
    if outcome.disposition == "REJECTED_PRESERVE":
        interactive_rendering.render_rejected_preserve(ctx, outcome, envelope.visible_text)
    else:
        interactive_rendering.render_controller_outcome(ctx, outcome)
    return False


def _send_pending(ctx: Any, controller: Any, text: str) -> bool:
    parts = text.strip().split()
    try:
        pending_id = int(parts[2])
    except (IndexError, ValueError):
        interactive_rendering.render_rejected_preserve(
            ctx,
            type("Rejected", (), {"disposition": "REJECTED_PRESERVE", "reason": "usage: /pending send <id>"})(),
            text,
        )
        return False
    pending_item = controller.pending.inspect(pending_id)
    outcome = controller.send_pending(
        pending_id,
        lambda submitted, cancel_event, register: execute_submission(ctx, submitted, cancel_event, register),
    )
    owner = pending_item.envelope.owner if pending_item is not None else "pending"
    _begin_run(ctx, outcome, owner)
    if outcome.disposition == "REJECTED_PRESERVE":
        interactive_rendering.render_rejected_preserve(ctx, outcome, text)
    else:
        interactive_rendering.render_controller_outcome(ctx, outcome)
    return False


def _handle_controller_input(text: str, ctx: Any, controller: Any) -> bool | None:
    interactive_rendering.drain_worker_stream(ctx)
    message = controller.poll_result()
    if message is not None:
        interactive_rendering.render_worker_message(ctx, message)
    entry = parse_action(text)
    if entry is not None and entry.action_id == "run.pending_send":
        return _send_pending(ctx, controller, text)
    if entry is not None and entry.binding.routing_kind == "AGENTIC":
        if agentic_payload(text, entry.action_id) is not None:
            return _submit_controller(ctx, controller, build_agentic_envelope(text, entry))
    elif entry is not None and entry.action_id in {
        "query.list_files", "query.read", "query.find", "query.git_status", "query.git_diff",
    }:
        if interactive_rendering.submit_query(text, ctx, entry.action_id):
            return False
    elif entry is None and text.strip().startswith("/"):
        interactive_rendering.render_rejected_preserve(
            ctx,
            type("Rejected", (), {"disposition": "REJECTED_PRESERVE", "reason": "UNKNOWN_COMMAND"})(),
            text,
        )
        return False
    elif entry is None:
        return _submit_controller(ctx, controller, build_natural_envelope(text))
    if entry is not None and entry.binding.busy_policy == "IDLE_ONLY" and controller.is_busy():
        interactive_rendering.render_rejected_preserve(
            ctx,
            type("Rejected", (), {"disposition": "REJECTED_PRESERVE", "reason": "REQUIRES_IDLE"})(),
            text,
        )
        return False
    return None


def handle_input(text: str, ctx: Any) -> bool:
    mailbox = getattr(ctx, "event_mailbox", None)
    view_model = getattr(ctx, "view_model", None)
    if mailbox is not None and view_model is not None:
        for envelope in mailbox.drain():
            view_model.apply(envelope.event)
    controller = getattr(ctx, "controller", None)
    if controller is not None:
        result = _handle_controller_input(text, ctx, controller)
        if result is not None:
            return result
    elif text.strip().startswith("/") and parse_action(text) is None:
        interactive_rendering.render_rejected_preserve(
            ctx,
            type("Rejected", (), {"disposition": "REJECTED_PRESERVE", "reason": "UNKNOWN_COMMAND"})(),
            text,
        )
        return False
    handled, should_exit = handle_command(text, ctx)
    if handled:
        return bool(should_exit)
    from agent.interfaces.cli.chat import run_agent_turn

    run_agent_turn(console, ctx, text)
    return False


__all__ = ["agentic_payload", "build_agentic_envelope", "build_natural_envelope", "confirm_exit", "handle_input"]
