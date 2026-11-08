"""Shared command-to-submission admission metadata; no rendering or stdin."""

from __future__ import annotations

from typing import Any

from llm_agent.interactive.action_parser import parse_action
from llm_agent.interactive.commands import CliActionMatch


def agentic_payload(text: str, command_id: str) -> str | None:
    match = parse_action(text)
    if command_id == "run.retry":
        return "/continue"
    if match is None or match.action_id != command_id:
        return None
    payload = match.raw_payload
    return (
        None if command_id == "interaction.code_submit" and payload.casefold() in {"help", "ajuda"} else payload or None
    )


def build_agentic_envelope(text: str, entry: CliActionMatch) -> Any:
    from llm_agent.interactive.controller import SubmissionEnvelope

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
    from llm_agent.interactive.controller import SubmissionEnvelope

    return SubmissionEnvelope(
        0,
        text,
        "natural_text",
        "AGENTIC",
        "AGENTIC_SUBMIT",
        "PENDING_EXACT_TEXT",
        text,
        "natural",
        "llm_agent.application.task_execution.execute_submission",
    )
