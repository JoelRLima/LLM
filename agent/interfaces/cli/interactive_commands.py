"""Interactive local controls and navigation handlers."""

from __future__ import annotations

from typing import Any

from agent.interfaces.cli.ui import console
from agent.interfaces.cli.workspace_entry import render_active_workspace


def _output_console() -> Any:
    try:
        from agent.interfaces.cli import command_handlers

        return command_handlers.console
    except (ImportError, AttributeError):
        return console


def _ui_print(ctx: Any, value: object) -> None:
    shell = getattr(ctx, "shell", None)
    if shell is not None:
        shell.print_background(value)
    else:
        _output_console().print(value, markup=False)


def _view_snapshot(ctx: Any) -> Any:
    view = getattr(ctx, "view_model", None)
    return view.snapshot() if view is not None else None


def status(_: str, ctx: Any) -> None:
    snapshot = _view_snapshot(ctx)
    controller = getattr(ctx, "controller", None)
    state = snapshot.state if snapshot is not None else (controller.state.value if controller is not None else "IDLE")
    profile = getattr(getattr(ctx, "session", None), "model_profile", None)
    model = getattr(profile, "model", "unknown")
    workspace = getattr(getattr(ctx, "workspace", None), "root", "unknown")
    mode = getattr(getattr(ctx, "orchestrator", None), "operational_mode_label", "unknown")
    pending_count = len(controller.pending.list()) if controller is not None else 0
    attention = bool(snapshot is not None and snapshot.attention_pending)
    _ui_print(
        ctx,
        f"workspace={workspace}\nmodel={model}\nmode={mode}\nstate={state}\n"
        f"pending={pending_count}\nattention={'yes' if attention else 'no'}",
    )


def where(_: str, ctx: Any) -> None:
    snapshot = _view_snapshot(ctx)
    if snapshot is None:
        _ui_print(ctx, "where: live UI projection unavailable")
        return
    activity = snapshot.current_tool or snapshot.current_step or (snapshot.milestones[-1] if snapshot.milestones else "unknown")
    milestones = ", ".join(snapshot.milestones[-8:]) or "none"
    _ui_print(
        ctx,
        f"run={snapshot.short_run_id or 'unknown'}\nstate={snapshot.state}\nowner={snapshot.owner or 'unknown'}\n"
        f"activity={activity}\nlast_activity={snapshot.last_activity_at or 'unknown'}\n"
        f"attention={'pending' if snapshot.attention_pending else 'none'}\nmilestones={milestones}",
    )


def timeline(_: str, ctx: Any) -> None:
    snapshot = _view_snapshot(ctx)
    if snapshot is None:
        _ui_print(ctx, "timeline: live UI projection unavailable")
        return
    text = "\n".join(f"{index + 1}. {item}" for index, item in enumerate(snapshot.milestones[-12:]))
    _ui_print(ctx, text or "(no milestones)")


def details(_: str, ctx: Any) -> None:
    snapshot = _view_snapshot(ctx)
    if snapshot is None:
        _ui_print(ctx, "details: live UI projection unavailable")
        return
    mailbox = getattr(ctx, "event_mailbox", None)
    stats = mailbox.stats() if mailbox is not None else None
    _ui_print(
        ctx,
        f"source=live_ui_projection\nrun={snapshot.short_run_id or 'unknown'}\nstate={snapshot.state}\n"
        f"model_active={snapshot.model_active if snapshot.model_active is not None else 'unknown'}\n"
        f"tool={snapshot.current_tool or 'unknown'}\nstep={snapshot.current_step or 'unknown'}\n"
        f"warnings={snapshot.warning_count} errors={snapshot.error_count}\n"
        f"mailbox_dropped={getattr(stats, 'dropped_milestones', 0)} "
        f"mailbox_coalesced={getattr(stats, 'coalesced_updates', 0)}",
    )


def pending(text: str, ctx: Any) -> None:
    controller = getattr(ctx, "controller", None)
    if controller is None:
        _ui_print(ctx, "pending: controller unavailable")
        return
    parts = text.strip().split()
    action = parts[1].casefold() if len(parts) >= 3 else ""
    if action in {"discard", "edit"}:
        try:
            pending_id = int(parts[2])
        except ValueError:
            _ui_print(ctx, "pending: id must be an integer")
            return
        if action == "discard":
            _ui_print(ctx, f"pending #{pending_id} discarded={controller.pending.discard(pending_id)}")
            return
        draft = controller.pending.load_for_editing(pending_id)
        if draft is None:
            _ui_print(ctx, "pending item not found")
            return
        ctx.draft_text = draft
        _ui_print(ctx, f"pending #{pending_id} loaded into the composer; edit and submit when ready")
        return
    items = controller.pending.list()
    _ui_print(ctx, "\n".join(f"#{item.pending_id}: {item.visible_text}" for item in items) or "(no pending follow-ups)")


def cancel(_: str, ctx: Any) -> None:
    controller = getattr(ctx, "controller", None)
    if controller is None:
        _ui_print(ctx, "cancel: controller unavailable")
        return
    outcome = controller.request_cancel()
    _ui_print(ctx, f"cancel: {outcome.disposition.lower()} generation={outcome.run_generation or 'none'}")


def attention(text: str, ctx: Any) -> None:
    broker = getattr(ctx, "approval_broker", None)
    if broker is None:
        _ui_print(ctx, "attention: broker unavailable")
        return
    current = broker.current()
    if current is None:
        _ui_print(ctx, "attention: none")
        return
    action = text.strip().split()[1].casefold() if len(text.strip().split()) > 1 else ""
    if action in {"approve", "aprovar", "yes", "sim", "deny", "negar", "no", "nao"}:
        decision = "approved" if action in {"approve", "aprovar", "yes", "sim"} else "rejected"
        result = broker.resolve(
            current.identity.attention_id,
            decision,
            run_generation=current.identity.run_generation,
            request_fingerprint=current.identity.request_fingerprint,
        )
        _ui_print(ctx, f"attention #{current.identity.attention_id}: {result}")
        return
    request = current.request
    if action in {"details", "detail", "full", "diff"}:
        proposed_diff = str(request.metadata.get("proposed_diff", "") or "")
        if not proposed_diff:
            _ui_print(ctx, "attention: no proposed diff is attached to this request")
            return
        suffix = "\n[diff truncated; review metadata preserves the original digest]" if request.metadata.get("proposed_diff_truncated") else ""
        _ui_print(ctx, f"attention #{current.identity.attention_id} proposed diff:\n{proposed_diff}{suffix}")
        return
    _ui_print(
        ctx,
        f"attention #{current.identity.attention_id} (default=deny/view details)\n"
        f"action={request.action}\nresource={request.resource}\nprompt={request.prompt}\n"
        "Use /attention details to inspect the bounded proposed diff, or /attention approve/deny.",
    )


def model(text: str, ctx: Any) -> None:
    profile = getattr(getattr(ctx, "session", None), "model_profile", None)
    parts = text.strip().split(maxsplit=2)
    if len(parts) >= 2 and parts[1].casefold() in {"select", "usar", "use"}:
        selected = parts[2].strip() if len(parts) >= 3 else ""
        profiles = getattr(ctx, "config", {}).get("model_profiles", {})
        if not selected:
            prompt_line = getattr(ctx, "prompt_line", None)
            if not callable(prompt_line):
                shell = getattr(ctx, "shell", None)
                prompt_line = getattr(shell, "prompt_line", None)
            if not callable(prompt_line):
                _ui_print(ctx, "model: profile selection requires the interactive selector")
                return
            from agent.interfaces.cli.selector import SelectorItem, TerminalSelector

            items = tuple(
                SelectorItem(
                    item_id=name,
                    label=name,
                    description=str(value.get("model", "")) if isinstance(value, dict) else "",
                )
                for name, value in sorted(profiles.items())
            )
            selected_result = TerminalSelector(
                prompt_line=prompt_line,
                emit=lambda value: _ui_print(ctx, value),
            ).choose(
                items,
                title="Selecione o perfil de modelo",
                default_id=getattr(ctx, "config", {}).get("default_model_profile"),
            )
            if selected_result.cancelled or selected_result.item_id is None:
                return
            selected = selected_result.item_id
        if selected not in profiles:
            _ui_print(ctx, f"model: profile desconhecido: {selected}")
            return
        controller = getattr(ctx, "controller", None)
        if controller is not None and controller.is_busy():
            _ui_print(ctx, "model: selection requires an idle session")
            return
        from agent.llm.model_profile import resolve_model_profile
        from agent.runtime.config_repository import ConfigRepository

        resolve_model_profile(ctx.config, profile_name=selected)
        ConfigRepository(ctx.app_paths, config_path=ctx.config_path).update({"default_model_profile": selected})
        ctx.rebootstrap_profile = selected
        _ui_print(ctx, f"model: profile {selected} selected; recreating the session")
        return
    suffix = "\nModel selection requires an idle session." if len(parts) > 1 else ""
    _ui_print(
        ctx,
        f"model={getattr(profile, 'model', 'unknown')} provider={getattr(profile, 'provider', 'unknown')}" + suffix,
    )


def show_workspace(text: str, ctx: Any) -> None:
    parts = text.strip().split(maxsplit=2)
    if len(parts) < 2 or parts[1].casefold() not in {"switch", "choose", "selecionar"}:
        render_active_workspace(_output_console(), ctx.workspace)
        return
    controller = getattr(ctx, "controller", None)
    broker = getattr(ctx, "approval_broker", None)
    query_executor = getattr(ctx, "query_executor", None)
    if controller is not None and controller.is_busy():
        _ui_print(ctx, "workspace: switching requires an idle agentic worker")
        return
    if broker is not None and broker.current() is not None:
        _ui_print(ctx, "workspace: resolve attention before switching")
        return
    if query_executor is not None and query_executor.is_busy():
        _ui_print(ctx, "workspace: cancel or wait for the active query before switching")
        return
    from agent.interfaces.cli.workspace_entry import (
        WorkspaceSelectionCancelled,
        canonical_workspace,
        choose_workspace,
        load_last_workspace,
    )
    from agent.interfaces.cli.workspace_recents import load_recent_workspaces

    requested = parts[2] if len(parts) == 3 else None
    try:
        target = canonical_workspace(requested) if requested else choose_workspace(
            console=_output_console(),
            current=ctx.workspace.root,
            last_workspace=load_last_workspace(ctx.app_paths),
            recent_workspaces=load_recent_workspaces(ctx.app_paths),
            prompt=getattr(ctx, "prompt_line", None),
            path_prompt=getattr(ctx, "prompt_path", None),
        )
    except WorkspaceSelectionCancelled:
        _ui_print(ctx, "workspace: seleção cancelada")
        return
    if target == ctx.workspace.root:
        _ui_print(ctx, "workspace: already active")
        return
    ctx.rebootstrap_workspace = target
    _ui_print(ctx, f"workspace: reopening quiescent session at {target}")


__all__ = [
    "attention",
    "cancel",
    "details",
    "model",
    "pending",
    "show_workspace",
    "status",
    "timeline",
    "where",
]
