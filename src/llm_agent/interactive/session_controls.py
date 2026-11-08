"""UI-neutral implementations of interactive session control actions."""

from __future__ import annotations

from concurrent.futures import Future
from typing import Any, cast

from llm_agent.interactive.commands import CliActionMatch
from llm_agent.interactive.futures import completed

CONTROL_ACTION_IDS = frozenset({
    "run.status", "run.where", "run.timeline", "run.details", "run.cancel",
    "run.pending_list", "run.pending_send", "run.pending_edit", "run.pending_discard",
    "run.attention_show", "run.attention_details", "run.attention_approve", "run.attention_deny",
})


def _result(answer: str, *, status: str = "succeeded") -> Future[Any]:
    return completed(({"status": status, "answer": answer}, None))


def _payload_id(match: CliActionMatch) -> int | None:
    parts = match.raw_payload.strip().split()
    if not parts:
        return None
    try:
        return int(parts[0])
    except ValueError:
        return None


def execute(session: Any, match: CliActionMatch) -> Future[Any]:
    """Execute one local control action using only session-owned state/services."""
    action = match.action_id
    if action not in CONTROL_ACTION_IDS:
        return _result(f"ACTION_UNAVAILABLE: {action}", status="failed")

    if action == "run.cancel":
        return _cancel(session)

    if action.startswith("run.attention_"):
        return _attention(session, action)

    if action.startswith("run.pending_"):
        return _pending(session, match, action)

    snapshot = session.snapshot()
    state = snapshot.get("state", "IDLE")
    if action == "run.status":
        return _status(session, snapshot, state)
    if action == "run.where":
        run_id = snapshot.get("short_run_id")
        if not run_id and state == "IDLE":
            return _result("Nenhuma tarefa em execução. Descreva seu objetivo no chat para começar.")
        activity = snapshot.get("current_tool") or snapshot.get("current_step")
        milestones = ", ".join(snapshot.get("milestones", ())[-8:]) or "Nenhum evento registrado"
        return _result(
            f"Execução: {run_id or 'Aguardando identificação'}\nEstado: {state}\n"
            f"Atividade: {activity or 'Aguardando atualização'}\n"
            f"Última atualização: {snapshot.get('last_activity_at') or 'Ainda não registrada'}\n"
            f"Aprovação: {'pendente; revise /attention' if snapshot.get('attention_pending') else 'nenhuma pendente'}\n"
            f"Eventos: {milestones}"
        )
    if action == "run.timeline":
        milestones = snapshot.get("milestones", ())
        return _result("\n".join(f"{index + 1}. {item}" for index, item in enumerate(milestones[-12:]))
                       or "Nenhum evento registrado.")
    mailbox = session.mailbox.stats()
    return _result(
        f"source=live_ui_projection\nrun={snapshot.get('short_run_id') or 'unknown'}\nstate={state}\n"
        f"model_active={snapshot.get('model_active', 'unknown')}\n"
        f"tool={snapshot.get('current_tool') or 'unknown'}\nstep={snapshot.get('current_step') or 'unknown'}\n"
        f"warnings={snapshot.get('warning_count', 0)} errors={snapshot.get('error_count', 0)}\n"
        f"mailbox_dropped={getattr(mailbox, 'dropped_milestones', 0)} "
        f"mailbox_coalesced={getattr(mailbox, 'coalesced_updates', 0)}"
    )


def _cancel(session: Any) -> Future[Any]:
    outcome = session.controller.request_cancel()
    broker = getattr(session, "approval_broker", None)
    if broker is not None:
        broker.invalidate()
    queries = getattr(session, "queries", None)
    if queries is not None:
        queries.request_cancel()
    message = (
        "Cancelamento solicitado; aguardando a tarefa encerrar."
        if outcome.disposition == "CANCEL_REQUESTED" else "Nenhum cancelamento solicitado."
    )
    return _result(f"{message} ({outcome.disposition.lower()})")


def _status(session: Any, snapshot: dict[str, Any], state: str) -> Future[Any]:
    info = session.presentation_snapshot()
    profile = info.get("profile") or {}
    return _result(
        f"workspace={info.get('workspace_root', session.workspace.root)}\n"
        f"model={profile.get('model', 'not configured')}\nmode={info.get('mode', 'READ_ONLY')} (session mode)\n"
        f"state={state}\npending={snapshot.get('pending_count', 0)}\n"
        f"attention={'yes' if snapshot.get('attention_pending') else 'no'}"
    )


def _pending(session: Any, match: CliActionMatch, action: str) -> Future[Any]:
    store = session.controller.pending
    pending_id = _payload_id(match)
    if action == "run.pending_list":
        items = store.list()
        return _result("\n".join(f"#{item.pending_id}: {item.visible_text}" for item in items)
                       or "(no pending follow-ups)")
    if pending_id is None:
        return _result("pending: id must be an integer", status="failed")
    if action == "run.pending_discard":
        return _result(f"pending #{pending_id} discarded={store.discard(pending_id)}")
    if action == "run.pending_edit":
        draft = store.load_for_editing(pending_id)
        if draft is None:
            return _result("pending item not found", status="failed")
        session.draft_text = draft
        return completed(({"status": "succeeded", "answer": f"pending #{pending_id} loaded into the composer; edit and submit when ready", "draft": draft}, None))
    if action == "run.pending_send":
        sender = getattr(session, "send_pending", None)
        if callable(sender):
            return cast(Future[Any], sender(pending_id))
        return _result("pending send is unavailable in this session adapter", status="failed")
    return _result("pending action unavailable", status="failed")


def _attention(session: Any, action: str) -> Future[Any]:
    broker = getattr(session, "approval_broker", None)
    if broker is None:
        return _result("attention: broker unavailable", status="failed")
    current = broker.current()
    if current is None:
        return _result("Nenhuma aprovação pendente.")
    identity = current.identity
    if action in {"run.attention_approve", "run.attention_deny"}:
        decision = "approved" if action == "run.attention_approve" else "rejected"
        result = broker.resolve(identity.attention_id, decision,
                                run_generation=identity.run_generation,
                                request_fingerprint=identity.request_fingerprint)
        return _result(f"attention #{identity.attention_id}: {result}")
    request = current.request
    if action == "run.attention_details":
        diff = str(request.metadata.get("proposed_diff", "") or "")
        if not diff:
            return _result("attention: no proposed diff is attached to this request")
        suffix = "\n[diff truncated; review metadata preserves the original digest]" if request.metadata.get("proposed_diff_truncated") else ""
        return _result(f"attention #{identity.attention_id} proposed diff:\n{diff}{suffix}")
    return _result(
        f"attention #{identity.attention_id} (explicit decision required; default: deny)\n"
        f"action={request.action}\nresource={request.resource}\nprompt={request.prompt}\n"
        "Review with /attention details, then explicitly approve or deny."
    )


def attention_snapshot(session: Any) -> dict[str, Any] | None:
    """Return the current broker prompt as bounded UI-safe metadata."""
    broker = getattr(session, "approval_broker", None)
    current = broker.current() if broker is not None else None
    if current is None:
        return None
    request = current.request
    identity = current.identity
    return {
        "attention_id": identity.attention_id,
        "run_generation": identity.run_generation,
        "request_fingerprint": identity.request_fingerprint,
        "action": request.action,
        "resource": request.resource,
        "prompt": request.prompt,
        "metadata": dict(request.metadata),
    }


def bind_attention(session: Any, envelope: Any, register: Any) -> Any:
    """Bind broker callbacks to the run's existing controller and view model."""
    broker = getattr(session, "approval_broker", None)
    if broker is None:
        return None
    generation = envelope.run_generation

    def arrived(_snapshot: Any) -> None:
        if session.view is not None:
            session.view.set_attention(True)
        if session.controller is not None:
            session.controller.mark_waiting_attention(generation)

    def cleared(_identity: Any) -> None:
        if session.view is not None:
            session.view.set_attention(False)
        if session.controller is not None:
            session.controller.mark_running(generation)

    broker.bind_generation(generation, on_attention=arrived, on_cleared=cleared)
    register(lambda: broker.invalidate(generation=generation))
    return broker


__all__ = ["CONTROL_ACTION_IDS", "attention_snapshot", "bind_attention", "execute"]
