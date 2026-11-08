"""Interactive query submission and presentation."""

from __future__ import annotations

from typing import Any

from llm_agent.application.services.queries import (
    QUERY_INVALID_REQUEST,
    WorkspaceQueryKind,
    WorkspaceQueryRequest,
    WorkspaceQueryResult,
    WorkspaceQueryStatus,
)
from llm_agent.interactive.query_syntax import _canonical_command_id, query_arguments
from llm_agent.interfaces.cli.output_projection import (
    format_workspace_query_result,
    publish_workspace_query_result,
    render_publication,
)
from llm_agent.interfaces.cli.query_executor import CliQueryCompletion
from llm_agent.interfaces.cli.ui import console
from llm_agent.outputs.models import OutputError


def _emit(ctx: Any, value: object) -> None:
    shell = getattr(ctx, "shell", None)
    if shell is not None:
        shell.print_background(value)
    else:
        console.print(value, markup=False)


def render_query_result(ctx: Any, result: Any) -> None:
    marker = getattr(result, "live_marker", None)
    if marker:
        _emit(ctx, marker)
    adapter_reason = getattr(result, "adapter_reason_code", None)
    if adapter_reason is not None:
        _emit(ctx, adapter_reason)
        return
    canonical = getattr(result, "result", result)
    if not isinstance(canonical, WorkspaceQueryResult):
        _emit(ctx, "query failed")
        return
    if canonical.status is not WorkspaceQueryStatus.SUCCEEDED:
        _emit(ctx, canonical.error or canonical.reason_code or "query failed")
        return
    text = format_workspace_query_result(canonical)
    output_service = getattr(ctx, "output_service", None)
    if output_service is None:
        _emit(ctx, text)
        return
    try:
        publication = publish_workspace_query_result(
            output_service,
            canonical,
            action_id=str(getattr(canonical.kind, "value", canonical.kind)),
        )
    except OutputError as exc:
        _emit(ctx, text)
        _emit(ctx, f"[output artifact unavailable: {exc.reason_code}]")
        return
    if publication is None:
        _emit(ctx, text)
        return
    render_publication(publication, lambda value: _emit(ctx, value))


def submit_query(text: str, ctx: Any, command_id: str) -> bool:
    command_id = _canonical_command_id(command_id)
    executor = getattr(ctx, "query_executor", None)
    service = getattr(ctx, "query_service", None)
    if executor is None or service is None:
        return False
    arguments = query_arguments(text, command_id)
    if arguments is None or "_error" in arguments:
        try:
            kind = WorkspaceQueryKind(command_id)
        except ValueError:
            kind = WorkspaceQueryKind.READ
        render_query_result(
            ctx,
            WorkspaceQueryResult(
                kind,
                WorkspaceQueryStatus.FAILED,
                reason_code=QUERY_INVALID_REQUEST,
                error=arguments and arguments.get("_error") or f"Uso: {text.split()[0]} <argumento>",
            ),
        )
        return True
    try:
        kind = WorkspaceQueryKind(command_id)
        request = WorkspaceQueryRequest(kind, arguments)
    except (TypeError, ValueError) as exc:
        render_query_result(
            ctx,
            WorkspaceQueryResult(
                WorkspaceQueryKind.READ,
                WorkspaceQueryStatus.FAILED,
                reason_code=QUERY_INVALID_REQUEST,
                error=str(exc),
            ),
        )
        return True
    submitted = executor.submit(
        request,
        task_active=bool(getattr(ctx, "controller", None) is not None and ctx.controller.is_busy()),
        execute=service.execute,
    )
    if isinstance(submitted, CliQueryCompletion):
        render_query_result(ctx, submitted)
    else:
        from llm_agent.interfaces.cli.interactive_rendering import render_controller_outcome

        render_controller_outcome(
            ctx,
            type("QueryAccepted", (), {"disposition": "ACCEPTED", "run_generation": submitted.query_generation})(),
        )
    return True


__all__ = ["query_arguments", "render_query_result", "submit_query"]
