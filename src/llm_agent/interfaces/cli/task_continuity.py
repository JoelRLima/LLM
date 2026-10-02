"""CLI adapters for model-free task continuity and explicit resume."""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, cast

from llm_agent.application.context import AppPaths
from llm_agent.application.task_continuity import (
    TaskContinuityRequest,
    TaskContinuityResult,
    read_task_continuity,
)
from llm_agent.application.task_execution import close_task_execution, execute_submission, observe_task_settlement
from llm_agent.interfaces.cli.workspace_entry import argument_workspace, require_task_workspace


def _value(args: Any, name: str, default: Any = None) -> Any:
    return getattr(args, name, default)


def _snapshot(args: Any) -> TaskContinuityResult:
    return read_task_continuity(
        TaskContinuityRequest(
            app_paths=AppPaths.discover(app_home=_value(args, "home")),
            workspace=argument_workspace(args),
        )
    )


def _document(snapshot: TaskContinuityResult) -> dict[str, Any]:
    return cast(dict[str, Any], snapshot.to_dict())


def _status(snapshot: TaskContinuityResult) -> str:
    status = cast(str, snapshot.status)
    return status.casefold()


def _print_json(document: Any) -> None:
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))


def _create_application(args: Any, *, configure_logging: bool) -> Any:
    from llm_agent.interfaces.cli.bootstrap import create_application

    return create_application(args, configure_logging=configure_logging)


def _print_receipt(result: Any) -> None:
    from llm_agent.interfaces.cli.operational_receipt import print_operational_receipt

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
        status = _status(snapshot)
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
    return 2 if _status(snapshot) == "invalid" else 0


def run_task_resume(
    args: Any,
    *,
    create_application: Callable[..., Any],
    print_json: Callable[[Any], None],
    print_receipt: Callable[[Any], None],
) -> int:
    """Preflight and route an explicit resume through AgentApplication."""

    require_task_workspace(args)
    snapshot = _snapshot(args)
    document = _document(snapshot)
    if not snapshot.resumable:
        reason = snapshot.reason_code
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
        settlement = execute_submission(application, "", entry="headless-resume")
    finally:
        close_task_execution(application)

    result = observe_task_settlement(settlement, channel="headless")
    if json_output:
        print_json(result)
    elif result["success"]:
        print(result["answer"])
        print_receipt(result)
    elif result.get("receipt"):
        if result["answer"]:
            print(result["answer"])
        print_receipt(result)
        if result["error"]:
            print(result["error"], file=sys.stderr)
    else:
        print(result["error"] or result["answer"] or "A retomada falhou.", file=sys.stderr)
    return 0 if result["success"] else 1


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
