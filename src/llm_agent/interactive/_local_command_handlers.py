"""Small UI neutral handlers for local interactive commands."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from llm_agent.interactive.thinking_presets import DEFAULT_THINKING_BUDGET, THINKING_PRESET_BY_KEY

if TYPE_CHECKING:
    from llm_agent.interactive.local_commands import LocalCommandService


def needs_input(field: str, usage: str) -> dict[str, Any]:
    return {"status": "needs_input", "answer": usage, "data": None, "input": field}


def configuration(service: LocalCommandService, action: str, payload: str) -> dict[str, Any]:
    if action == "configuration.system_show":
        data = service.read_conversation()
        return ok(data.effective_system_prompt, data)
    if action == "configuration.system_set":
        if not payload:
            return needs_input("system_prompt", "Usage: /system <prompt text>")
        data = service.set_system_prompt(payload)
        return ok("System prompt updated.", data)
    if action == "configuration.thinking_toggle":
        current = service.read_conversation().thinking_budget
        budget = _thinking_budget(payload) if payload or not current else 0
        data = service.set_thinking_budget(budget)
        answer = f"Thinking enabled (budget: {data.thinking_budget})." if budget else "Thinking disabled."
        return ok(answer, data)
    if action == "configuration.debug_toggle":
        level = service.diagnostics()
        return ok(("Diagnostics off", "Diagnostics on", "Diagnostics verbose")[level], level)
    if action == "configuration.mode_show" and not payload:
        data = service.execution_mode()
        return ok(data.label, data)
    if action in {"configuration.mode_set", "configuration.mode_show"}:
        if not payload:
            return needs_input("execution_mode", "Usage: /mode <read-only|editor|full>")
        data = service.execution_mode(payload.removeprefix("set ").strip())
        answer = data.label or data.status
        return ok(answer, data) if data.status == "selected" else error(answer, data)
    raise ValueError(action)


def history(service: LocalCommandService, action: str, payload: str, context: Any | None) -> dict[str, Any]:
    if action == "clear":
        service.history("clear")
        return ok("History cleared.", None)
    path = storage_path(context, "chat_history_file", "chat_history.json", payload)
    if path is None:
        return needs_input("history_path", "Usage: /history save [path] or /history load [path]")
    data = service.history(cast("Literal['clear', 'save', 'load']", action), path)
    return {"status": "ok" if data.success else "error", "answer": data.message, "data": data}


def memory(service: LocalCommandService, action: str, payload: str, context: Any | None) -> dict[str, Any]:
    args = payload.split(maxsplit=1)
    if action == "remember":
        if len(args) < 2:
            return needs_input("memory_key_value", "Usage: /memory remember <key> <value>")
        data = service.memory(cast("Literal['remember', 'show', 'forget', 'clear', 'save', 'load']", action), key=args[0], value=args[1])
        answer = f"Remembered {args[0]} = {args[1]}"
    elif action == "forget":
        if not args:
            return needs_input("memory_key", "Usage: /memory forget <key>")
        data = service.memory(cast("Literal['remember', 'show', 'forget', 'clear', 'save', 'load']", action), key=args[0])
        answer = f"Forgot {args[0]} (if present)."
    elif action in {"save", "load"}:
        path = storage_path(context, "memory_file", "agent_memory.json", payload)
        if path is None:
            return needs_input("memory_path", f"Usage: /memory {action} [path]")
        data = service.memory(cast("Literal['remember', 'show', 'forget', 'clear', 'save', 'load']", action), path=path)
        answer = str(data.get("message", ""))
    else:
        data = service.memory(cast("Literal['remember', 'show', 'forget', 'clear', 'save', 'load']", action))
        answer = "Session memory cleared." if action == "clear" else str(data.get("rows", ""))
    if data.get("ok") is False or data.get("success") is False:
        return error(str(data.get("message") or data.get("error") or "Memory command failed"), data)
    return ok(answer, data)


def workspace_query(service: LocalCommandService, action_id: str, payload: str) -> dict[str, Any]:
    action = {"query.list_files": "list", "query.read": "read", "query.find": "find"}[action_id]
    if action != "list" and not payload:
        usage = "Usage: /read <path>" if action == "read" else "Usage: /find <text>"
        return needs_input(f"{action}_value", usage)
    data = service.workspace_command(cast("Literal['list', 'read', 'find', 'search']", action), payload)
    answer = str(data.get("data") or data.get("error") or "")
    return {"status": "ok" if data.get("ok") else "error", "answer": answer, "data": data}


def doctor_projection(
    *, app_paths: Any = None, workspace: Any = None, config_path: str | Path | None = None,
    profile: str | None = None, raw_payload: str = "",
) -> dict[str, Any]:
    """Run the canonical health use case and project it without a task runtime."""
    from llm_agent.application.health import HealthDiagnosticsRequest, run_health_diagnostics

    flags = set(raw_payload.split())
    data = run_health_diagnostics(HealthDiagnosticsRequest(
        write_report="--write-report" in flags,
        online="--online" in flags,
        app_paths=app_paths,
        workspace=workspace,
        config_path=config_path,
        profile=profile,
    ))
    return ok(data.rendered_report, data)


def ok(answer: str, data: Any) -> dict[str, Any]:
    return {"status": "ok", "answer": answer, "data": data}


def error(answer: str, data: Any) -> dict[str, Any]:
    return {"status": "error", "answer": answer, "data": data}


def _thinking_budget(payload: str) -> int:
    key = payload.upper()
    return max(0, THINKING_PRESET_BY_KEY[key] if key in THINKING_PRESET_BY_KEY else int(payload) if payload else DEFAULT_THINKING_BUDGET)


def storage_path(context: Any | None, key: str, fallback: str, payload: str) -> str | None:
    if payload:
        selected = payload.strip()
        if len(selected) >= 2 and selected[0] == selected[-1] and selected[0] in {"'", '"'}:
            selected = selected[1:-1]
        return selected
    if context is None:
        return fallback
    paths = getattr(context, "workspace_paths", None)
    if key == "chat_history_file":
        field = "chat_history_file"
    elif key == "memory_file":
        field = "memory_file"
    else:
        return fallback
    resolved_path = getattr(paths, field, None)
    return fallback if resolved_path is None else str(resolved_path)


__all__ = ["configuration", "doctor_projection", "error", "history", "memory", "needs_input", "ok", "storage_path", "workspace_query"]
