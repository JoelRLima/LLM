"""CLI adapters for model-free task continuity and explicit resume."""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

from agent.interfaces.cli.workspace_entry import argument_workspace
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext


def _value(args: Any, name: str, default: Any = None) -> Any:
    return getattr(args, name, default)


def _snapshot(args: Any) -> Any:
    from agent.continuity import TaskContinuityService

    workspace_context = WorkspaceContext.create(argument_workspace(args))
    workspace_paths = AppPaths.discover(app_home=_value(args, "home")).for_workspace(
        workspace_context.workspace_id
    )
    return TaskContinuityService(workspace_paths).snapshot()


def _document(snapshot: Any) -> dict[str, Any]:
    to_dict = getattr(snapshot, "to_dict", None)
    if not callable(to_dict):
        raise TypeError("snapshot de continuidade invalido")
    document = to_dict()
    if not isinstance(document, dict):
        raise TypeError("snapshot de continuidade invalido")
    return document


def _status(snapshot: Any, document: dict[str, Any]) -> str:
    value = document.get("status", getattr(snapshot, "status", "invalid"))
    return str(getattr(value, "value", value)).casefold()


def _print_json(document: Any) -> None:
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))


def _create_application(args: Any, *, configure_logging: bool) -> Any:
    from agent.interfaces.cli.bootstrap import create_application

    return create_application(args, configure_logging=configure_logging)


def _print_receipt(result: Any) -> None:
    from agent.interfaces.cli.operational_receipt import print_operational_receipt

    print_operational_receipt(result)


def run_task_status(
    args: Any,
    *,
    print_json: Callable[[Any], None],
) -> int:
    """Render a bounded read-only continuity projection."""

    snapshot = _snapshot(args)
    document = _document(snapshot)
    if bool(_value(args, "json_output", False)):
        print_json(document)
    else:
        status = _status(snapshot, document)
        print(f"Task continuity: {status.upper()}")
        print(f"Objective: {document.get('objective_preview') or '(none)'}")
        print(f"Root task: {document.get('root_task_id') or '(none)'}")
        continuity = document.get("continuity")
        continuity_map = continuity if isinstance(continuity, dict) else {}
        print(f"Resume generation: {continuity_map.get('resume_generation', 0)}")
        checkpoint_label = (
            "valid"
            if document.get("checkpoint_present") and status != "invalid"
            else "absent"
            if not document.get("checkpoint_present")
            else "invalid"
        )
        print(f"Checkpoint: {checkpoint_label}")
        print(f"Resume: {'available' if document.get('resumable') else 'unavailable'}")
        related = document.get("related_runs")
        if isinstance(related, list) and related:
            latest = related[0] if isinstance(related[0], dict) else {}
            print(f"Latest run: {latest.get('liveness', 'unavailable')}")
        if status in {"terminal", "unsupported", "invalid"}:
            print(f"Reason: {document.get('reason_code', 'CHECKPOINT_INVALID')}")
            print("Checkpoint preserved: yes")
    return 2 if _status(snapshot, document) == "invalid" else 0


def run_task_resume(
    args: Any,
    *,
    create_application: Callable[..., Any],
    print_json: Callable[[Any], None],
    print_receipt: Callable[[Any], None],
) -> int:
    """Preflight and route an explicit resume through AgentApplication."""

    snapshot = _snapshot(args)
    document = _document(snapshot)
    if not bool(document.get("resumable", getattr(snapshot, "resumable", False))):
        reason = str(document.get("reason_code") or "TASK_NOT_RESUMABLE")
        message = f"A tarefa não pode ser retomada: {reason}."
        if bool(_value(args, "json_output", False)):
            print_json(
                {
                    "status": "failed",
                    "success": False,
                    "answer": "",
                    "error": message,
                    "reason_code": reason,
                    "continuity": document,
                }
            )
        else:
            print(message, file=sys.stderr)
        return 2

    json_output = bool(_value(args, "json_output", False))
    application = create_application(args, configure_logging=not json_output)
    try:
        resume = getattr(application, "resume", None)
        result = resume() if callable(resume) else application.run(None, explicit_resume=True)
    finally:
        application.close()

    if json_output:
        print_json(result.to_dict())
    elif result.success:
        print(result.answer)
        print_receipt(result)
    elif getattr(result, "receipt", None):
        if result.answer:
            print(result.answer)
        print_receipt(result)
        if result.error:
            print(result.error, file=sys.stderr)
    else:
        print(result.error or result.answer or "A retomada falhou.", file=sys.stderr)
    return 0 if result.success else 1


def dispatch_task(
    args: Any,
    *,
    run_context: Callable[[Any], int],
) -> int:
    command = _value(args, "task_command")
    if command == "context":
        return run_context(args)
    if command == "status":
        return run_task_status(args, print_json=_print_json)
    if command == "resume":
        return run_task_resume(
            args,
            create_application=_create_application,
            print_json=_print_json,
            print_receipt=_print_receipt,
        )
    raise ValueError("subcomando task desconhecido")


__all__ = ["dispatch_task", "run_task_resume", "run_task_status"]
