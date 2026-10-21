"""Private finite memory, workspace and diagnostics operations."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event
from typing import Any, cast


def _execute_memory_command(
    orchestrator: Any, action: str, *, key: str, value: str, path: str | None,
) -> dict[str, Any]:
    if action == "remember":
        orchestrator.remember(key, value)
    elif action == "forget":
        orchestrator.forget(key)
    elif action == "clear":
        orchestrator.clear_memory()
    elif action == "show":
        return {"rows": [(str(section), str(content)) for section, content in
                         orchestrator.agent_state.memory.state.items() if content]}
    elif action == "save":
        return {"message": str(orchestrator.save_memory_to_file(path))}
    elif action == "load":
        return {"message": str(orchestrator.load_memory_from_file(path))}
    else:
        raise ValueError("unsupported memory command")
    return {}


def _prepare_workspace_command(action: str, value: str) -> tuple[str, dict[str, str]]:
    commands = {"list": ("directory_lister", {"path": "."}),
                "read": ("file_reader", {"file_path": value}),
                "find": ("grep", {"pattern": value, "path": "."}),
                "search": ("web_search", {"query": value})}
    if action not in commands:
        raise ValueError("unsupported workspace command")
    return commands[action]


def _execute_workspace_command(
    orchestrator: Any, name: str, arguments: dict[str, str], *,
    cancellation_event: Event | None, primitive: Callable[[Any], Any],
) -> dict[str, Any]:
    gateway = getattr(orchestrator, "tool_invocation_gateway", None)
    if gateway is None:
        return {"status": "unavailable", "error": f"{name} indisponível", "data": None}
    kwargs: dict[str, Any] = {"active_skills": None,
                              "allowed_capabilities": getattr(orchestrator, "allowed_capabilities", None)}
    if cancellation_event is not None:
        kwargs["cancellation_token"] = cancellation_event
    return cast(dict[str, Any], primitive(gateway.run(name, arguments, **kwargs).to_legacy_dict()))


def _set_session_diagnostics(orchestrator: Any, enabled: bool) -> None:
    orchestrator.verbose = enabled
    orchestrator.context_manager.verbose = orchestrator.verbose
