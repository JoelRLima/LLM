"""Shared adapters for the typed code and web-search execution boundaries."""
from __future__ import annotations

from typing import Any, Callable

from llm_agent.application.code_commands import execute_code_command
from llm_agent.application.local_code import execute_local_code_command, is_local_code_command
from llm_agent.application.task_execution import execute_workspace_command, read_execution_capabilities


def execute_search(ctx: Any, envelope: Any, cancel_event: Any) -> Any:
    return execute_workspace_command(ctx.task_execution, "search", envelope.payload.strip(), cancellation_event=cancel_event)


def execute_code(ctx: Any, envelope: Any, cancel_event: Any, register: Callable[..., Any], *, executor: Callable[..., Any] = execute_code_command, capabilities: Callable[..., Any] = read_execution_capabilities) -> Any:
    if is_local_code_command(envelope.visible_text):
        root = ctx.workspace.root if getattr(ctx, "workspace", None) is not None else "."
        return execute_local_code_command(envelope.visible_text, root, register_cancellation=register,
                                          cancellation_requested=cancel_event.is_set)
    broker = getattr(ctx, "approval_broker", None)
    return executor(
        envelope.visible_text,
        config=ctx.config,
        conversation=ctx.conversation,
        workspace_root=ctx.workspace.root if getattr(ctx, "workspace", None) is not None else ".",
        allows_write_validate=lambda: capabilities(ctx.task_execution).allows_write_validate,
        is_full_mode=lambda: capabilities(ctx.task_execution).is_full_mode,
        approval_factory=lambda _assume_yes: broker.approve_change if broker is not None else None,
        register_cancellation=register,
        cancellation_requested=cancel_event.is_set,
    )


def execute_workbench_command(session: Any, runtime: Any, action_id: str, envelope: Any, cancel_event: Any,
                              register: Callable[..., Any], *, publish_text: Callable[[str], None]) -> tuple[dict[str, Any], Any]:
    """Project the same typed code/search results for the graphical adapter."""
    from types import SimpleNamespace

    from llm_agent.application.interactive_worker import run_interactive_worker
    from llm_agent.application.task_execution import read_execution_context

    context = read_execution_context(runtime)
    ctx = SimpleNamespace(task_execution=runtime, config=context.config, conversation=context.conversation,
                          workspace=context.workspace, approval_broker=session.approval_broker)
    if action_id == "query.web_search":
        result = run_interactive_worker(lambda: execute_search(ctx, envelope, cancel_event), publish_text=publish_text)
        return {"status": "succeeded" if result.get("ok") else "failed",
                "answer": str(result.get("data") or result.get("error") or result)}, None
    outcome = run_interactive_worker(lambda: execute_code(ctx, envelope, cancel_event, register), publish_text=publish_text)
    answer = outcome.help_text or outcome.answer or outcome.error or outcome.summary
    if outcome.artifacts:
        answer += "\n\n" + "\n\n".join(str(content or kind) for kind, content in outcome.artifacts)
    reference = session._preview("Resultado de /code", answer[:262144], "CodingApplicationService", truncated=len(answer) > 262144)
    return {"status": outcome.status, "answer": answer[:64000]}, reference
