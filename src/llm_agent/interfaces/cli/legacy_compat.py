"""Compatibility bridge for old lightweight test facades.

The real application path never enters these fallbacks: it exposes the W12
``interact`` boundary.  Keeping the shim isolated lets historical offline
fixtures continue to exercise the pre-W12 facade without becoming routing
authority.
"""

from __future__ import annotations

from typing import Any

from llm_agent.application.conversation import append_legacy_transcript
from llm_agent.application.task_execution import TaskDispatch, execute_submission


def dispatch_task_facade(ctx: Any, request: TaskDispatch) -> Any:
    return execute_submission(ctx.task_execution, "", entry="headless-resume" if request.continues_task else "headless-run", dispatch=request)

def append_legacy_answer(ctx: Any, answer: str) -> None:
    append_legacy_transcript(ctx.conversation, answer)


def append_legacy_turn(ctx: Any, text: str, answer: str) -> None:
    append_legacy_transcript(ctx.conversation, answer, user_text=text)


def dispatch_natural_facade(ctx: Any, text: str) -> Any:
    return execute_submission(ctx.task_execution, text, entry="natural")


__all__ = [
    "append_legacy_answer",
    "append_legacy_turn",
    "dispatch_natural_facade",
    "dispatch_task_facade",
]
