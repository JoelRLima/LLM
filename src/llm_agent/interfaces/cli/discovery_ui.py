"""Interactive /commands action owner and non-executing palette projection."""

from __future__ import annotations

from typing import Any, cast

from llm_agent.discovery.contracts import DiscoveryResultV1
from llm_agent.interfaces.cli.action_registry import DEFAULT_CLI_ACTION_REGISTRY
from llm_agent.interfaces.cli.discovery_projection import discover_commands, record_discovery_use
from llm_agent.interfaces.cli.ui import PRIMARY_ACTION_IDS


def _engineering_views(ctx: Any) -> tuple[object, ...]:
    from llm_agent.application.context import AppPaths, WorkspaceContext
    from llm_agent.interfaces.cli.engineering import discovery_operation_views

    paths = getattr(ctx, "app_paths", None) or AppPaths.discover()
    workspace = getattr(ctx, "workspace", None)
    return discovery_operation_views(paths, workspace if isinstance(workspace, WorkspaceContext) else None)


def commands_action(text: str, ctx: Any) -> None:
    match = DEFAULT_CLI_ACTION_REGISTRY.match(text)
    query = match.raw_payload.strip() if match is not None else ""
    result = cast(
        DiscoveryResultV1,
        discover_commands(
            query,
            app_paths=getattr(ctx, "app_paths", None),
            controller=getattr(ctx, "controller", None),
            workspace_bound=getattr(ctx, "workspace", None) is not None,
            engineering_views=_engineering_views(ctx),
            entry_ids=frozenset(f"action:{action_id}" for action_id in PRIMARY_ACTION_IDS) if not query else None,
        ),
    )
    candidates = result.candidates
    shell = getattr(ctx, "shell", None)
    if shell is not None:
        prompt_line = getattr(ctx, "prompt_line", None)
        if not callable(prompt_line):
            prompt_line = getattr(shell, "prompt_line", None)
        if candidates and callable(prompt_line):
            from llm_agent.interfaces.cli.selector import SelectorItem, TerminalSelector

            items = tuple(
                SelectorItem(
                    item_id=candidate.entry.entry_id,
                    label=candidate.entry.preferred_invocation,
                    description=candidate.entry.description,
                    disabled_reason=None if candidate.available else (candidate.disabled_reason or "unavailable"),
                )
                for candidate in candidates
            )
            selected = TerminalSelector(
                prompt_line=prompt_line,
                emit=lambda value: shell.print_background(value),
            ).choose(items, title="Comandos principais — /commands <busca> encontra outros recursos", allow_command_draft=True)
            if selected.draft is not None:
                shell.take_palette_return_draft() if callable(getattr(shell, "take_palette_return_draft", None)) else None
                shell.set_draft(selected.draft)
            elif selected.cancelled:
                prior = shell.take_palette_return_draft() if callable(getattr(shell, "take_palette_return_draft", None)) else None
                if prior:
                    shell.set_draft(prior)
            else:
                candidate = next(
                    (item for item in candidates if item.entry.entry_id == selected.item_id),
                    None,
                )
                if candidate is not None:
                    select_palette_entry(candidate, ctx)
            return
        lines: list[str] = []
        for candidate in candidates:
            suffix = "" if candidate.available else f" [{candidate.disabled_reason or 'unavailable'}]"
            lines.append(f"{candidate.entry.preferred_invocation} — {candidate.entry.description}{suffix}")
        shell.print_background("\n".join(lines) or "Nenhum comando encontrado. Tente /commands com outra palavra ou /help.")
        prior = shell.take_palette_return_draft() if callable(getattr(shell, "take_palette_return_draft", None)) else None
        if prior:
            shell.set_draft(prior)
        return
    from llm_agent.interfaces.cli.ui import console

    lines = [
        f"{candidate.entry.preferred_invocation} - {candidate.entry.description}"
        + ("" if candidate.available else f" [{candidate.disabled_reason or 'unavailable'}]")
        for candidate in candidates
    ]
    rendered = "\n".join(lines) or "Nenhum comando encontrado. Tente /commands com outra palavra ou /help."
    console.print(rendered, markup=False)


def select_palette_entry(candidate: Any, ctx: Any) -> None:
    """Only drafts/renders a selected item; the palette never executes it."""

    shell = getattr(ctx, "shell", None)
    if shell is None:
        return
    prior = shell.take_palette_return_draft() if callable(getattr(shell, "take_palette_return_draft", None)) else None
    entry = getattr(candidate, "entry", None)
    if entry is None:
        if prior:
            shell.set_draft(prior)
        return
    try:
        record_discovery_use(entry.entry_id, getattr(ctx, "app_paths", None))
    except Exception:
        pass  # Optional ranking state must not block a selected invocation.
    invocation = str(getattr(entry, "preferred_invocation", ""))
    source_kind = getattr(getattr(entry, "source_kind", None), "value", None)
    if source_kind == "action":
        shell.set_draft(invocation)
    else:
        shell.print_background(invocation)
        if prior:
            shell.set_draft(prior)


def f2_items(ctx: Any) -> tuple[object, ...]:
    result = cast(
        DiscoveryResultV1,
        discover_commands(
            "",
            app_paths=getattr(ctx, "app_paths", None),
            controller=getattr(ctx, "controller", None),
            workspace_bound=getattr(ctx, "workspace", None) is not None,
            engineering_views=_engineering_views(ctx),
        ),
    )
    return result.candidates


__all__ = ["commands_action", "f2_items", "select_palette_entry"]
