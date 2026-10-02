"""Rich/terminal adapter for the UI-neutral inspection API."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rich.console import Console

from llm_agent.application.agent_boundary import InspectionQuery, InspectionService, InspectorSnapshot
from llm_agent.application.context import AppPaths, WorkspaceContext
from llm_agent.application.inspection import (
    BookmarkAddRequest,
    BookmarkListRequest,
    BookmarkRemoveRequest,
    DiagnosticExportRequest,
    InspectionAuxiliaryOperations,
)
from llm_agent.application.task_execution import read_runtime_inspection
from llm_agent.interfaces.cli.inspector_rendering import (
    render_runs as _render_runs_to_console,
)
from llm_agent.interfaces.cli.inspector_rendering import (
    render_snapshot as _render_snapshot_to_console,
)

console = Console()


def _value(args: Any, name: str, default: Any = None) -> Any:
    return getattr(args, name, default)


def _workspace_paths(args: Any) -> Any:
    workspace = WorkspaceContext.create(Path(_value(args, "workspace", Path.cwd())).expanduser())
    return AppPaths.discover(app_home=_value(args, "home")).for_workspace(workspace.workspace_id)


def _service(args: Any) -> InspectionService:
    return InspectionService(_workspace_paths(args))


def _query(args: Any) -> InspectionQuery:
    sequence_end = _value(args, "inspect_sequence_end")
    if sequence_end is None:
        sequence_end = _value(args, "inspect_sequence")
    return InspectionQuery.build(
        sequence_start=_value(args, "inspect_sequence_start"),
        sequence_end=sequence_end,
        sources=_value(args, "inspect_sources"),
        event_kinds=_value(args, "inspect_kinds"),
        activity_categories=_value(args, "inspect_categories"),
        severities=_value(args, "inspect_severities"),
        statuses=_value(args, "inspect_statuses"),
        task_id=_value(args, "inspect_task_id"),
        root_task_id=_value(args, "inspect_root_task_id"),
        step=_value(args, "inspect_step"),
        correlation_id=_value(args, "inspect_correlation_id"),
        invocation_id=_value(args, "inspect_invocation_id"),
        time_start=_value(args, "inspect_time_start"),
        time_end=_value(args, "inspect_time_end"),
        search=_value(args, "search"),
        bookmarked_only=bool(_value(args, "inspect_bookmarked_only", False)),
    )


def _after(args: Any) -> int:
    value = _value(args, "after", 0)
    if value is None:
        return 0
    if isinstance(value, bool):
        raise ValueError("--after requer uma sequência inteira não-negativa")
    try:
        selected = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("--after requer uma sequência inteira não-negativa") from exc
    if selected < 0:
        raise ValueError("--after requer uma sequência inteira não-negativa")
    return selected


def _is_tty() -> bool:
    return bool(getattr(sys.stdout, "isatty", lambda: False)() and getattr(sys.stdin, "isatty", lambda: False)())


def render_snapshot(snapshot: InspectorSnapshot, *, limit: int | None = None) -> None:
    _render_snapshot_to_console(snapshot, console, limit=limit)


def _render_runs(runs: tuple[Any, ...]) -> None:
    _render_runs_to_console(runs, console)


def _print_json(document: Any) -> None:
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))


def _run_follow(service: InspectionService, run_id: str | None, args: Any) -> int:
    if _value(args, "json_output", False):
        raise ValueError("--json não pode ser combinado com follow sem limite temporal")
    if not _is_tty():
        raise ValueError("--follow requer terminal interativo; use inspect show --json para snapshot bounded")
    after = _after(args)
    try:
        while True:
            snapshot = service.snapshot(
                run_id,
                query=_query(args),
                limit=_value(args, "limit", 100),
                selected_sequence=_value(args, "inspect_sequence"),
                after_sequence=after,
            )
            console.clear()
            render_snapshot(snapshot)
            if snapshot.timeline:
                after = max(after, snapshot.timeline[-1].sequence)
            after = max(after, snapshot.run.highest_sequence)
            liveness = snapshot.run.liveness
            if isinstance(liveness, Mapping) and liveness.get("state") in {"closed", "stale"}:
                return 0
            time.sleep(0.25)
    except (KeyboardInterrupt, EOFError):
        console.print("Inspector detached; run unchanged.", markup=False)
        return 0


def _run_export(args: Any, operations: InspectionAuxiliaryOperations) -> int:
    receipt = operations.export_diagnostics(
        DiagnosticExportRequest(
            run_id=_value(args, "inspect_run_id"),
            output=_value(args, "output"),
            force=bool(_value(args, "force", False)),
            include_bookmarks=bool(_value(args, "include_bookmarks", False)),
        )
    )
    if _value(args, "json_output", False):
        _print_json(receipt.to_dict())
    else:
        print(f"Exported: {receipt.path}")
    return 0


def _run_bookmark(args: Any, operations: InspectionAuxiliaryOperations) -> int:
    run_id = _value(args, "inspect_run_id")
    command = _value(args, "bookmark_command")
    if command == "add":
        add_result = operations.add_bookmark(
            BookmarkAddRequest(
                run_id=run_id,
                sequence=_value(args, "inspect_sequence"),
                note=_value(args, "note"),
            )
        )
        document: Any = add_result.to_dict()
    elif command == "remove":
        remove_result = operations.remove_bookmark(
            BookmarkRemoveRequest(
                run_id=run_id,
                sequence=_value(args, "inspect_sequence"),
            )
        )
        document = remove_result.to_dict()
    else:
        list_result = operations.list_bookmarks(BookmarkListRequest(run_id=run_id))
        document = list_result.to_dict()
    if _value(args, "json_output", False):
        _print_json(document)
    else:
        print(json.dumps(document, ensure_ascii=False, sort_keys=True))
    return 0


def _run_list(args: Any, service: InspectionService) -> int:
    runs = service.list_runs(limit=_value(args, "limit", 100))
    if _value(args, "json_output", False):
        _print_json({"runs": [item.to_dict() for item in runs]})
    else:
        _render_runs(runs)
    return 0


def _run_replay(args: Any, service: InspectionService, run_id: str | None) -> int:
    query = _query(args)
    selected = service.select(run_id)
    activities = service.replay(
        selected.metadata.run_id,
        query=query,
        limit=_value(args, "limit", 100),
        after_sequence=_after(args),
    )
    document = {
        "run": selected.metadata.to_dict(),
        "completeness": selected.read_result.completeness.value,
        "issues": list(selected.read_result.issues),
        "activities": [item.to_dict() for item in activities],
    }
    if _value(args, "json_output", False):
        _print_json(document)
    else:
        for item in activities:
            print(f"#{item.sequence} {item.title}: {item.summary}")
        print(f"completeness={document['completeness']}")
    return 0


def _run_snapshot(args: Any, service: InspectionService, run_id: str | None) -> int:
    snapshot = service.snapshot(
        run_id,
        query=_query(args),
        limit=_value(args, "limit", 100),
        selected_sequence=_value(args, "inspect_sequence"),
        after_sequence=_after(args),
    )
    if _value(args, "json_output", False):
        _print_json(snapshot.to_dict())
    else:
        render_snapshot(snapshot)
    return 0


def run_inspect(args: Any) -> int:
    """Dispatch the installed inspect surface without constructing AgentApplication."""

    command = _value(args, "inspect_command")
    run_id = _value(args, "inspect_run_id")
    if command == "list":
        service = _service(args)
        return _run_list(args, service)
    if command == "bookmark":
        operations = InspectionAuxiliaryOperations(_workspace_paths(args))
        return _run_bookmark(args, operations)
    if command == "export":
        operations = InspectionAuxiliaryOperations(_workspace_paths(args))
        return _run_export(args, operations)
    service = _service(args)
    if _value(args, "follow", False):
        return _run_follow(service, run_id, args)
    if command is None and _is_tty() and not _value(args, "json_output", False):
        return _run_follow(service, run_id, args)
    if command == "replay":
        return _run_replay(args, service, run_id)
    return _run_snapshot(args, service, run_id)


def render_context_inspect(context: Any) -> None:
    """Render `/inspect` through the application's already-created service."""

    render_snapshot(read_runtime_inspection(context.task_execution))


__all__ = ["render_context_inspect", "run_inspect", "render_snapshot"]
