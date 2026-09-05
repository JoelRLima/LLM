"""Compatibility bridge for old lightweight test facades.

The real application path never enters these fallbacks: it exposes the W12
``interact`` boundary.  Keeping the shim isolated lets historical offline
fixtures continue to exercise the pre-W12 facade without becoming routing
authority.
"""

from __future__ import annotations

from typing import Any

from agent.interfaces.task_directives import ParsedTaskRequest


def dispatch_task_facade(ctx: Any, request: ParsedTaskRequest) -> Any:
    if request.action.value == "continue":
        return ctx.application.resume()
    directive = request.directive
    if directive is None:
        raise ValueError("RUN requires a TaskRunDirective")
    return ctx.application.run(directive.subject, task_run_directive=directive)


def append_legacy_answer(ctx: Any, answer: str) -> None:
    ctx.session.add_assistant_message(answer)


def append_legacy_turn(ctx: Any, text: str, answer: str) -> None:
    ctx.session.add_user_message(text)
    ctx.session.add_assistant_message(answer)


def dispatch_natural_facade(ctx: Any, text: str) -> Any:
    return ctx.application.run(text)


__all__ = [
    "append_legacy_answer",
    "append_legacy_turn",
    "dispatch_natural_facade",
    "dispatch_task_facade",
]
