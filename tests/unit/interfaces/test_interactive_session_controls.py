from __future__ import annotations

from types import SimpleNamespace

from llm_agent.application.approval import ApprovalPrompt
from llm_agent.interactive.action_parser import parse_action
from llm_agent.interactive.attention import ApprovalBroker
from llm_agent.interactive.controller import InteractiveExecutionController
from llm_agent.interactive.controller_models import SubmissionEnvelope
from llm_agent.interactive.futures import completed
from llm_agent.interactive.session_controls import (
    CONTROL_ACTION_IDS,
    attention_snapshot,
    bind_attention,
    execute,
)
from llm_agent.interactive.ui_plane import RunViewModel


class _Mailbox:
    def stats(self):
        return SimpleNamespace(dropped_milestones=2, coalesced_updates=3)


def _session():
    return SimpleNamespace(
        controller=InteractiveExecutionController(),
        view=RunViewModel(),
        mailbox=_Mailbox(),
        workspace=SimpleNamespace(root="/workspace"),
        presentation_snapshot=lambda: {"workspace_root": "/workspace", "mode": "READ_ONLY"},
        snapshot=lambda: {
            "state": "RUNNING", "short_run_id": "12345678", "milestones": ("started", "reading"),
            "pending_count": 0, "attention_pending": False, "current_tool": "read", "current_step": "inspect",
            "last_activity_at": "now", "model_active": True, "warning_count": 1, "error_count": 0,
        },
    )


def _run(session, command):
    match = parse_action(command)
    assert match is not None and match.action_id in CONTROL_ACTION_IDS
    return execute(session, match).result()


def test_controls_project_status_timeline_and_details_from_session_services():
    session = _session()
    status, reference = _run(session, "/status")
    assert reference is None
    assert "workspace=/workspace" in status["answer"]
    assert "state=RUNNING" in status["answer"]
    timeline, _ = _run(session, "/timeline")
    assert timeline["answer"] == "1. started\n2. reading"
    details, _ = _run(session, "/details")
    assert "mailbox_dropped=2 mailbox_coalesced=3" in details["answer"]


def test_pending_edit_discard_and_send_delegate_to_session():
    session = _session()
    envelope = SubmissionEnvelope(0, "follow up", "natural_text", "AGENTIC", "AGENTIC_SUBMIT",
                                 "PENDING_EXACT_TEXT", "follow up", "natural", "test")
    item = session.controller.pending.add(envelope)
    edited, _ = _run(session, f"/pending edit {item.pending_id}")
    assert "loaded into the composer" in edited["answer"]
    assert session.draft_text == "follow up"
    session.send_pending = lambda pending_id: completed(
        ({"status": "succeeded", "answer": f"sent {pending_id}"}, None)
    )
    sent, _ = _run(session, f"/pending send {item.pending_id}")
    assert sent["answer"] == f"sent {item.pending_id}"
    discarded, _ = _run(session, f"/pending discard {item.pending_id}")
    assert "discarded=True" in discarded["answer"]


def test_attention_helpers_bind_run_callbacks_and_resolve_identity_safely():
    session = _session()
    session.approval_broker = ApprovalBroker()
    envelope = SimpleNamespace(run_generation=7)
    callbacks = []
    broker = bind_attention(session, envelope, callbacks.append)
    assert broker is session.approval_broker
    arrived = []
    cleared = []
    broker.bind_generation(7, on_attention=arrived.append, on_cleared=cleared.append)
    import threading

    result = []
    ready = threading.Event()
    broker.bind_generation(7, on_attention=lambda snapshot: (arrived.append(snapshot), ready.set()), on_cleared=cleared.append)
    worker = threading.Thread(target=lambda: result.append(broker.present(ApprovalPrompt("write", "a.txt", "Allow?", {}))))
    worker.start()
    assert ready.wait(2)
    projected = attention_snapshot(session)
    assert projected and projected["action"] == "write"
    match = parse_action("/attention approve")
    assert match is not None
    observation, _ = execute(session, match).result()
    assert "RESOLVED" in observation["answer"]
    worker.join(2)
    assert not worker.is_alive()
    assert cleared
    assert callbacks
