"""Model-free CLI adapter for reading persisted task authority."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from llm_agent.application.context import AppPaths
from llm_agent.application.task_context import TaskContextRequest, read_task_context


def run_task_context(
    args: Any,
    *,
    app_paths: AppPaths,
    workspace: str | Path,
    print_json: Callable[[Any], None],
) -> int:
    result = read_task_context(
        TaskContextRequest(
            app_paths=app_paths,
            workspace=workspace,
            task_id=str(args.task_id),
            phase_id=getattr(args, "phase_id", None),
        )
    )
    if bool(getattr(args, "json_output", False)):
        print_json(result.to_dict())
    else:
        print(result.trusted_text)
    return 0
