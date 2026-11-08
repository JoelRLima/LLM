"""Interpret submission syntax without admitting or owning execution."""

from __future__ import annotations

from concurrent.futures import Future
from threading import Event
from typing import TYPE_CHECKING, Any, Callable, Literal, NamedTuple

from llm_agent.application.local_code import execute_local_code_command, is_local_code_command
from llm_agent.application.services.queries import WorkspaceQueryKind, WorkspaceQueryRequest
from llm_agent.application.task_execution import read_execution_context
from llm_agent.interactive import session_controls, session_llm, session_navigation
from llm_agent.interactive.action_parser import parse_action
from llm_agent.interactive.commands import DEFAULT_COMMAND_REGISTRY, CliActionMatch
from llm_agent.interactive.futures import completed
from llm_agent.interactive.local_commands import (
    COMMAND_ACTION_IDS,
    LocalCommandService,
    execute_command,
    execute_doctor_command,
)
from llm_agent.interactive.query_syntax import query_arguments

if TYPE_CHECKING:
    from llm_agent.interactive.session import InteractiveSession

QUERY_ACTIONS = {
    "query.list_files": WorkspaceQueryKind.LIST_FILES,
    "query.read": WorkspaceQueryKind.READ,
    "query.find": WorkspaceQueryKind.FIND,
    "query.git_status": WorkspaceQueryKind.GIT_STATUS,
    "query.git_diff": WorkspaceQueryKind.DIFF,
}


class SubmissionRoute(NamedTuple):
    match: CliActionMatch | None
    entry: Literal["retry", "agent", "natural"]
    payload: str


def route_submission(
    self: InteractiveSession,
    text: str,
    _on_admitted: Callable[[Future[Any]], None] | None,
) -> SubmissionRoute | Future[Any]:
    _validate_submission(self, text)
    match = parse_action(text)
    if match is None:
        if text.lstrip().startswith("/"):
            raise ValueError("UNKNOWN_COMMAND: comando não reconhecido; use /commands")
        if not self.llm_enabled:
            return _schedule_project_command(self, text, _on_admitted)
        return SubmissionRoute(None, "natural", text)
    routed = _route_local_action(self, text, match, _on_admitted)
    if routed is not None:
        return routed
    return _route_agentic(match)


def _validate_submission(self: InteractiveSession, text: str) -> None:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty submission")
    if len(text) > self._max_input_chars:
        raise ValueError("INPUT_TOO_LARGE")


def _route_local_action(
    self: InteractiveSession,
    text: str,
    match: CliActionMatch,
    on_admitted: Callable[[Future[Any]], None] | None,
) -> SubmissionRoute | Future[Any] | None:
    action = match.action_id
    if action == "process.exec":
        return _schedule_project_command(self, match.raw_payload, on_admitted)
    if action in session_llm.ACTIONS:
        future = session_llm.execute(self, match.raw_payload)
        _notify_admitted(on_admitted, future)
        return future
    if action == "interaction.code_submit":
        return _route_local_code(self, text, on_admitted)
    if action in QUERY_ACTIONS:
        return workspace_query(self, text, action, on_admitted)
    if action in session_controls.CONTROL_ACTION_IDS:
        future = session_controls.execute(self, match)
        _notify_admitted(on_admitted, future)
        return future
    if action in {"discovery.help", "discovery.commands"}:
        return command_help(on_admitted, match.raw_payload)
    if action in COMMAND_ACTION_IDS or action in session_navigation.ACTIONS:
        return _schedule_local(self, match, on_admitted)
    if action == "session.exit":
        future = completed(({"status": "succeeded", "answer": "Sessão encerrada.", "session_exit": True}, None))
        _notify_admitted(on_admitted, future)
        return future
    return None


def _route_local_code(
    self: InteractiveSession,
    text: str,
    on_admitted: Callable[[Future[Any]], None] | None,
) -> Future[Any] | None:
    if not is_local_code_command(text):
        return None

    def analyze(_envelope: Any, cancel: Event, register: Any) -> Any:
        outcome = execute_local_code_command(
            text,
            self.workspace.root,
            register_cancellation=register,
            cancellation_requested=cancel.is_set,
        )
        answer = outcome.help_text or outcome.answer or outcome.error or outcome.summary
        if outcome.artifacts:
            answer += "\n\n" + "\n\n".join(
                str(content or kind) for kind, content in outcome.artifacts
            )
        reference = self._preview(
            "Analise local de codigo",
            answer[:262144],
            "CodingApplicationService",
            truncated=len(answer) > 262144,
        )
        return {"status": outcome.status, "answer": answer[:64000], "model_used": False}, reference

    return self._controlled(
        analyze,
        owner="local_code",
        track_activity=False,
        on_admitted=on_admitted,
    )


def _schedule_local(
    self: InteractiveSession,
    match: CliActionMatch,
    on_admitted: Callable[[Future[Any]], None] | None,
) -> Future[Any]:
    action = match.action_id

    def local(_envelope: Any, _cancel: Event, _register: Any) -> Any:
        if action in session_navigation.ACTIONS:
            return session_navigation.execute(self, match)
        if action == "maintenance.doctor":
            outcome = execute_doctor_command(
                app_paths=self.start.app_paths,
                workspace=self.workspace,
                config_path=self.start.config_path,
                profile=self.start.profile,
                raw_payload=match.raw_payload,
            )
            status = "succeeded" if outcome["status"] == "ok" else "failed"
            return {"status": status, "answer": outcome["answer"]}, None
        return _execute_application_command(self, action, match)

    return self._controlled(local, owner=action, track_activity=False, on_admitted=on_admitted)


def _schedule_project_command(
    self: InteractiveSession,
    command: str,
    on_admitted: Callable[[Future[Any]], None] | None,
) -> Future[Any]:
    from llm_agent.application.project_command import execute_project_command

    def execute(_envelope: Any, cancellation: Event, _register: Any) -> tuple[dict[str, Any], None]:
        return execute_project_command(command, self.workspace.root, cancellation), None

    return self._controlled(
        execute,
        owner="process.exec",
        track_activity=False,
        on_admitted=on_admitted,
    )


def _execute_application_command(
    self: InteractiveSession,
    action: str,
    match: CliActionMatch,
) -> tuple[dict[str, Any], None]:
    runtime = self._ensure_runtime()
    context = read_execution_context(runtime)
    service = LocalCommandService(
        runtime,
        context.conversation,
        app_paths=self.start.app_paths,
        workspace=self.workspace,
        config_path=self.start.config_path,
        profile=self.start.profile,
        diagnostic_level=getattr(self, "diagnostic_level", 0),
    )
    outcome = execute_command(service, action, match.raw_payload, context=context)
    self.diagnostic_level = service._diagnostic_level
    status = "succeeded" if outcome["status"] == "ok" else "failed"
    return {
        "status": status,
        "answer": outcome["answer"],
        "input_required": outcome["status"] == "needs_input",
    }, None


def _route_agentic(match: CliActionMatch) -> SubmissionRoute:
    action = match.action_id
    if action not in {"interaction.agent_submit", "run.retry", "interaction.code_submit", "query.web_search"}:
        raise ValueError("ACTION_UNAVAILABLE: esta ação ainda não tem adaptador nesta interface")
    entry: Literal["retry", "agent", "natural"] = "retry" if action == "run.retry" else "agent"
    payload = "/continue" if entry == "retry" else match.raw_payload
    if not payload.strip():
        raise ValueError("A solicitação precisa de um objetivo.")
    return SubmissionRoute(match, entry, payload)


def _notify_admitted(callback: Callable[[Future[Any]], None] | None, future: Future[Any]) -> None:
    if callback is not None:
        callback(future)


def workspace_query(
    self: InteractiveSession,
    text: str,
    action: str,
    on_admitted: Callable[[Future[Any]], None] | None,
) -> Future[Any]:
    arguments = query_arguments(text, action)
    if arguments is None or "_error" in arguments:
        raise ValueError("QUERY_INVALID_REQUEST: confira os argumentos do comando")
    return self.query(WorkspaceQueryRequest(QUERY_ACTIONS[action], arguments), _on_admitted=on_admitted)


def command_help(_on_admitted: Callable[[Future[Any]], None] | None, query: str = "") -> Future[Any]:
    lines = ["Comandos da sessão (use os argumentos após o comando):"]
    for binding in DEFAULT_COMMAND_REGISTRY._bindings:
        definition = DEFAULT_COMMAND_REGISTRY.catalog.get(binding.action_id)
        line = " ".join(binding.preferred_path) + " — " + definition.description
        if not query or query.casefold() in (line + " " + definition.title).casefold():
            lines.append(line)
    future = completed(({"status": "succeeded", "answer": "\n".join(lines)}, None))
    _notify_admitted(_on_admitted, future)
    return future


__all__ = ["QUERY_ACTIONS", "SubmissionRoute", "command_help", "route_submission", "workspace_query"]
