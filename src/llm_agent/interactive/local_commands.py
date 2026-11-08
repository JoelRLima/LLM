"""UI-neutral command use cases shared by local interactive interfaces.

The service returns application results and never renders text or prompts. An
interface gathers any missing input, calls one method, then presents its result.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from llm_agent.application.context import AppPaths, WorkspaceContext
from llm_agent.application.conversation import (
    ConversationRuntime,
    configure_conversation,
    execute_history_command,
    read_conversation,
)
from llm_agent.application.session_diagnostics import apply_interactive_diagnostic_mode
from llm_agent.application.task_execution import (
    TaskExecutionRuntime,
    execute_memory_command,
    execute_workspace_command,
    read_execution_capabilities,
    select_execution_mode,
)
from llm_agent.interactive import _local_command_handlers as handlers


class LocalCommandService:
    """Commands available in a local interactive session, independent of UI."""

    def __init__(
        self,
        runtime: TaskExecutionRuntime,
        conversation: ConversationRuntime,
        *,
        app_paths: AppPaths | None = None,
        workspace: WorkspaceContext | str | Path | None = None,
        config_path: str | Path | None = None,
        profile: str | None = None,
        diagnostic_level: int = 0,
    ) -> None:
        self.runtime = runtime
        self._conversation = conversation
        self.app_paths = app_paths
        self.workspace = workspace
        self.config_path = config_path
        self.profile = profile
        self._diagnostic_level = diagnostic_level

    def read_conversation(self) -> Any:
        return read_conversation(self._conversation)

    def set_system_prompt(self, value: str) -> Any:
        configure_conversation(self._conversation, system_prompt=value)
        return read_conversation(self._conversation)

    def set_thinking_budget(self, value: int) -> Any:
        if value < 0:
            raise ValueError("thinking budget must be non-negative")
        configure_conversation(self._conversation, thinking_budget=value)
        return read_conversation(self._conversation)

    def history(self, action: Literal["clear", "save", "load"], path: str | Path | None = None) -> Any:
        return execute_history_command(self._conversation, action, str(path or "chat_history.json"))

    def memory(
        self,
        action: Literal["remember", "show", "forget", "clear", "save", "load"],
        *, key: str = "", value: str = "", path: str | Path | None = None,
    ) -> dict[str, Any]:
        return execute_memory_command(self.runtime, action, key=key, value=value, path=None if path is None else str(path))

    def execution_mode(self, value: str | None = None) -> Any:
        if value is None:
            return read_execution_capabilities(self.runtime)
        return select_execution_mode(self.runtime, value)

    def diagnostics(self, level: int | None = None) -> int:
        if level is None:
            self._diagnostic_level = (self._diagnostic_level + 1) % 3
        elif level not in {0, 1, 2}:
            raise ValueError("diagnostic level must be 0, 1, or 2")
        else:
            self._diagnostic_level = level
        apply_interactive_diagnostic_mode(
            self.runtime, self._diagnostic_level,
            session_diagnostics_enabled=self._diagnostic_level >= 1,
        )
        return self._diagnostic_level

    def doctor(self, *, write_report: bool = False, online: bool = False) -> Any:
        from llm_agent.application.health import HealthDiagnosticsRequest, run_health_diagnostics

        return run_health_diagnostics(HealthDiagnosticsRequest(
            write_report=write_report, online=online, app_paths=self.app_paths,
            workspace=self.workspace, config_path=self.config_path, profile=self.profile,
        ))

    def workspace_command(self, action: Literal["list", "read", "find", "search"], value: str = "") -> dict[str, Any]:
        return execute_workspace_command(self.runtime, action, value)


def local_service_for_context(context: Any) -> LocalCommandService:
    """Bind the shared service to the runtime fields exposed by an interface context."""
    return LocalCommandService(
        context.task_execution, context.conversation,
        app_paths=getattr(context, "app_paths", None), workspace=getattr(context, "workspace", None),
        config_path=getattr(context, "config_path", None),
        profile=getattr(context, "config", {}).get("default_model_profile"),
        diagnostic_level=getattr(context, "modo_diagnostico", 0),
    )


__all__ = ["LocalCommandService"]


COMMAND_ACTION_IDS = frozenset({
    "configuration.system_show", "configuration.system_set", "configuration.thinking_toggle",
    "history.clear", "history.save", "history.load", "configuration.debug_toggle",
    "memory.show", "memory.remember", "memory.forget", "memory.clear", "memory.save", "memory.load",
    "configuration.mode_show", "configuration.mode_set", "maintenance.doctor",
    "query.list_files", "query.read", "query.find",
})


def execute_command(
    service: LocalCommandService, action_id: str, raw_payload: str = "", *, context: Any | None = None,
) -> dict[str, Any]:
    """Execute a canonical local action and return a small presentation-neutral result."""
    if action_id not in COMMAND_ACTION_IDS:
        return handlers.error(f"Unsupported local command: {action_id}", None)
    payload = raw_payload.strip()
    try:
        if action_id.startswith("configuration."):
            return handlers.configuration(service, action_id, payload)
        if action_id.startswith("history."):
            return handlers.history(service, action_id.partition(".")[2], payload, context)
        if action_id.startswith("memory."):
            return handlers.memory(service, action_id.partition(".")[2], payload, context)
        if action_id == "maintenance.doctor":
            return handlers.doctor_projection(
                app_paths=service.app_paths, workspace=service.workspace,
                config_path=service.config_path, profile=service.profile, raw_payload=payload,
            )
        if action_id.startswith("query."):
            return handlers.workspace_query(service, action_id, payload)
        raise ValueError(action_id)
    except (OSError, ValueError, TypeError) as exc:
        return handlers.error(str(exc), None)


def execute_doctor_command(
    *, app_paths: AppPaths | None = None, workspace: WorkspaceContext | str | Path | None = None,
    config_path: str | Path | None = None, profile: str | None = None, raw_payload: str = "",
) -> dict[str, Any]:
    """Run `/doctor` before a task runtime exists, using only application context."""
    try:
        return handlers.doctor_projection(
            app_paths=app_paths, workspace=workspace, config_path=config_path,
            profile=profile, raw_payload=raw_payload.strip(),
        )
    except (OSError, ValueError, TypeError) as exc:
        return handlers.error(str(exc), None)


__all__ = ["LocalCommandService", "local_service_for_context", "COMMAND_ACTION_IDS", "execute_command", "execute_doctor_command"]
