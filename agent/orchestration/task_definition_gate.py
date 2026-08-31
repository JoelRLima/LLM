"""TaskRunner admission gate for durable task-definition authority."""

from __future__ import annotations

from typing import Any

from agent.planning.task_completion import mark_terminal_blocked
from agent.task_definition.errors import TaskDefinitionError, public_task_definition_error


def ensure_task_definition(runner: Any, inputs: Any) -> str | None:
    compiler = getattr(runner.orchestrator, "task_definition_compiler", None)
    state = runner.orchestrator.agent_state
    try:
        if compiler is None or not callable(getattr(compiler, "compile", None)):
            raise TaskDefinitionError(
                "compiler de task definition indisponivel",
                code="TASK_DEFINITION_COMPILER_UNAVAILABLE",
            )
        root_task_id = getattr(state, "root_task_id", None)
        if not isinstance(root_task_id, str) or not root_task_id.strip():
            raise TaskDefinitionError("root_task_id ausente para task definition")
        if inputs.resumed:
            reference = getattr(state, "task_definition_ref", None)
            if reference is None:
                raise TaskDefinitionError(
                    "checkpoint sem TaskDefinitionRef compacta",
                    code="TASK_DEFINITION_BINDING_MISSING",
                )
            reference = compiler.resume(root_task_id, reference)
        else:
            reference = compiler.compile(root_task_id, inputs.objective)
        state.task_definition_ref = reference
        resolver = getattr(runner.orchestrator, "task_context_resolver", None)
        if resolver is None or not callable(getattr(resolver, "resolve", None)):
            raise TaskDefinitionError(
                "resolver de task definition indisponivel",
                code="TASK_DEFINITION_RESOLVER_UNAVAILABLE",
            )
        resolver.resolve(reference)
        if runner.orchestrator._save_checkpoint() is False:
            raise TaskDefinitionError(
                "checkpoint de task definition nao foi confirmado",
                code="TASK_DEFINITION_CHECKPOINT_FAILED",
            )
        return None
    except TaskDefinitionError as exc:
        preserve_task_definition_checkpoint(runner)
        return mark_terminal_blocked(
            runner.orchestrator,
            reason_code=exc.reason_code,
            message=public_task_definition_error(exc),
            status="block",
        )


def preserve_task_definition_checkpoint(runner: Any) -> None:
    runner.orchestrator._preserve_checkpoint = True
    compiler = getattr(runner.orchestrator, "task_definition_compiler", None)
    partial = getattr(compiler, "last_ref", None)
    if partial is not None:
        runner.orchestrator.agent_state.task_definition_ref = partial
    runner.orchestrator._save_checkpoint()
