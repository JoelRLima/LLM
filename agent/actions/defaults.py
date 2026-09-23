"""Closed W19 product action catalog."""

from __future__ import annotations

from agent.actions.catalog import ActionCatalog
from agent.actions.models import ActionCategory as C
from agent.actions.models import ActionDefinition as D
from agent.actions.models import ActionPayloadKind as P
from agent.actions.models import ActionScope as S
from agent.actions.models import ActionTarget as T


def _d(
    action_id: str,
    title: str,
    category: C,
    scope: S,
    target: T,
    payload_kind: P,
    description: str,
    payload_hint: str | None = None,
    keywords: tuple[str, ...] = (),
    discovery_examples: tuple[str, ...] = (),
) -> D:
    return D(action_id, title, description, category, scope, target, payload_kind, payload_hint, keywords, discovery_examples)


DEFAULT_ACTION_CATALOG = ActionCatalog(
    (
        _d("discovery.help", "Help", C.DISCOVERY, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Show available commands."),
        _d("discovery.commands", "Commands", C.DISCOVERY, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.OPTIONAL_TEXT, "Search available commands and capabilities without executing them.", "query", ("commands", "discover", "search"), ("/commands", "/commands query")),
        _d("run.status", "Status", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Show the current run and session state."),
        _d("run.where", "Where", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Show the current run activity."),
        _d("run.timeline", "Timeline", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Show recent run milestones."),
        _d("run.details", "Details", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Show bounded technical run details."),
        _d("run.cancel", "Cancel run", C.RUN, S.PRODUCT, T.APPLICATION_CONTROL, P.NONE, "Request cancellation of the active run."),
        _d("run.pending_list", "Pending", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "List pending follow-up submissions."),
        _d("run.pending_send", "Send pending", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.TYPED, "Send one pending follow-up by its identifier.", "pending id"),
        _d("run.pending_edit", "Edit pending", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.TYPED, "Load one pending follow-up for editing.", "pending id"),
        _d("run.pending_discard", "Discard pending", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.TYPED, "Discard one pending follow-up.", "pending id"),
        _d("run.attention_show", "Attention", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Show the current approval request."),
        _d("run.attention_details", "Attention details", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Show bounded details for the current approval request."),
        _d("run.attention_approve", "Approve attention", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Approve the current approval request."),
        _d("run.attention_deny", "Deny attention", C.RUN, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Deny the current approval request."),
        _d("model.show", "Model", C.MODEL, S.PRODUCT, T.CONFIGURATION, P.NONE, "Show the active model profile."),
        _d("model.select", "Select model", C.MODEL, S.PRODUCT, T.CONFIGURATION, P.OPTIONAL_TEXT, "Select a model profile.", "profile"),
        _d("configuration.system_show", "Show system", C.CONFIGURATION, S.PRODUCT, T.APPLICATION_CONTROL, P.NONE, "Show the active system prompt."),
        _d("configuration.system_set", "Set system", C.CONFIGURATION, S.PRODUCT, T.APPLICATION_CONTROL, P.OPTIONAL_TEXT, "Set the system prompt."),
        _d("configuration.thinking_toggle", "Thinking", C.CONFIGURATION, S.PRODUCT, T.APPLICATION_CONTROL, P.OPTIONAL_TEXT, "Toggle or set the thinking budget."),
        _d("history.clear", "Clear history", C.HISTORY, S.PRODUCT, T.APPLICATION_CONTROL, P.NONE, "Clear the chat history."),
        _d("history.save", "Save history", C.HISTORY, S.PRODUCT, T.APPLICATION_CONTROL, P.OPTIONAL_TEXT, "Save the chat history."),
        _d("history.load", "Load history", C.HISTORY, S.PRODUCT, T.APPLICATION_CONTROL, P.OPTIONAL_TEXT, "Load chat history."),
        _d("configuration.debug_toggle", "Debug", C.CONFIGURATION, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "Toggle diagnostic display."),
        _d("memory.show", "Memory", C.MEMORY, S.PRODUCT, T.APPLICATION_CONTROL, P.NONE, "Show session memory."),
        _d("memory.remember", "Remember", C.MEMORY, S.PRODUCT, T.APPLICATION_CONTROL, P.REQUIRED_TEXT, "Store a memory key and value.", "key value"),
        _d("memory.forget", "Forget", C.MEMORY, S.PRODUCT, T.APPLICATION_CONTROL, P.OPTIONAL_TEXT, "Remove a memory key.", "key"),
        _d("memory.clear", "Clear memory", C.MEMORY, S.PRODUCT, T.APPLICATION_CONTROL, P.NONE, "Clear session memory."),
        _d("memory.save", "Save memory", C.MEMORY, S.PRODUCT, T.APPLICATION_CONTROL, P.OPTIONAL_TEXT, "Save session memory."),
        _d("memory.load", "Load memory", C.MEMORY, S.PRODUCT, T.APPLICATION_CONTROL, P.OPTIONAL_TEXT, "Load session memory."),
        _d("maintenance.doctor", "Doctor", C.MAINTENANCE, S.PRODUCT, T.APPLICATION_CONTROL, P.OPTIONAL_TEXT, "Run the bounded health diagnostic."),
        _d("query.list_files", "List files", C.QUERY, S.PRODUCT, T.WORKSPACE_QUERY, P.OPTIONAL_TEXT, "List confined workspace files.", "path"),
        _d("workspace.show", "Workspace", C.WORKSPACE, S.PRODUCT, T.APPLICATION_CONTROL, P.NONE, "Show the active workspace."),
        _d("workspace.switch", "Switch workspace", C.WORKSPACE, S.PRODUCT, T.APPLICATION_CONTROL, P.OPTIONAL_TEXT, "Switch to a workspace.", "path"),
        _d("configuration.mode_show", "Mode", C.CONFIGURATION, S.PRODUCT, T.APPLICATION_CONTROL, P.NONE, "Show the operational mode."),
        _d("configuration.mode_set", "Set mode", C.CONFIGURATION, S.PRODUCT, T.APPLICATION_CONTROL, P.REQUIRED_TEXT, "Set the operational mode.", "read-only|editor|full"),
        _d("run.retry", "Retry", C.RUN, S.PRODUCT, T.INTERACTION, P.NONE, "Retry the previous task."),
        _d("inspection.open", "Inspect", C.INSPECTION, S.PRODUCT, T.INSPECTION, P.OPTIONAL_TEXT, "Inspect the current run or an output."),
        _d("interaction.agent_submit", "Agent", C.INTERACTION, S.PRODUCT, T.INTERACTION, P.REQUIRED_TEXT, "Submit an agent task.", "objective"),
        _d("interaction.code_submit", "Code", C.INTERACTION, S.PRODUCT, T.CODE_WORKFLOW, P.REQUIRED_TEXT, "Submit a code workflow; use /code help for command details.", "code request"),
        _d("query.read", "Read", C.QUERY, S.PRODUCT, T.WORKSPACE_QUERY, P.REQUIRED_TEXT, "Read a confined workspace file.", "file"),
        _d("query.find", "Find", C.QUERY, S.PRODUCT, T.WORKSPACE_QUERY, P.REQUIRED_TEXT, "Find text in the confined workspace.", "pattern"),
        _d("query.git_status", "Git status", C.QUERY, S.PRODUCT, T.WORKSPACE_QUERY, P.NONE, "Show bounded Git status."),
        _d("query.git_diff", "Git diff", C.QUERY, S.PRODUCT, T.WORKSPACE_QUERY, P.OPTIONAL_TEXT, "Show a bounded Git diff.", "pathspecs"),
        _d("query.web_search", "Search", C.QUERY, S.PRODUCT, T.WEB_SEARCH, P.REQUIRED_TEXT, "Search the web.", "query"),
        _d("session.exit", "Exit", C.SESSION, S.INTERFACE_SESSION, T.INTERFACE_SESSION, P.NONE, "End the interactive session."),
    )
)


__all__ = ["DEFAULT_ACTION_CATALOG"]
