from concurrent.futures import Future

import pytest

from llm_agent.interactive.admission import build_natural_envelope
from llm_agent.interfaces.workbench.webview import WorkbenchBridge
from tests.unit.interfaces.test_shared_interactive_core import create_session


def test_snapshot_exposes_detached_exact_queue_without_consumption_or_execution(tmp_path):
    session = create_session(tmp_path)
    try:
        first = session.controller.pending.add(build_natural_envelope('Leia "notes.txt"\n<script>texto literal</script>'))
        session.controller.pending.add(build_natural_envelope("Segundo pedido"))
        bridge = WorkbenchBridge(session=session)
        snapshot = bridge.task_status()
        assert snapshot["workspace_id"] == session.workspace.workspace_id
        assert snapshot["controller_state"] == "IDLE"
        assert snapshot["pending_count"] == 2
        assert snapshot["pending_items"] == [
            {"pending_id": first.pending_id, "text": 'Leia "notes.txt"\n<script>texto literal</script>'},
            {"pending_id": first.pending_id + 1, "text": "Segundo pedido"},
        ]
        snapshot["pending_items"][0]["text"] = "altered outside owner"
        assert session.snapshot()["pending_items"][0]["text"] == first.visible_text
        assert len(session.controller.pending.list()) == 2
        assert session._runtime is None
    finally:
        session.close().result(timeout=5)


def test_pending_send_consumes_only_the_admitted_exact_request_once(tmp_path, monkeypatch):
    session = create_session(tmp_path)
    try:
        first = session.controller.pending.add(build_natural_envelope("Original exact request"))
        second = session.controller.pending.add(build_natural_envelope("Keep this queued"))
        calls = []

        def admitted(text):
            calls.append(text)
            future = Future()
            future.set_result(({"status": "succeeded", "answer": "synthetic"}, None))
            return future

        monkeypatch.setattr(session, "submit", admitted)
        session.send_pending(first.pending_id).result()
        assert calls == ["Original exact request"]
        assert session.snapshot()["pending_items"] == [{"pending_id": second.pending_id, "text": second.visible_text}]
        with pytest.raises(ValueError, match="not found"):
            session.send_pending(first.pending_id)
        assert len(calls) == 1
    finally:
        session.close().result(timeout=5)


@pytest.mark.parametrize("busy", [False, True])
def test_failed_or_busy_admission_preserves_pending_request(tmp_path, monkeypatch, busy):
    session = create_session(tmp_path)
    try:
        item = session.controller.pending.add(build_natural_envelope("Must remain queued"))

        def rejected(_text):
            raise RuntimeError("admission failed")

        monkeypatch.setattr(session, "submit", rejected)
        if busy:
            session._active = Future()
        with pytest.raises(RuntimeError):
            session.send_pending(item.pending_id)
        assert session.controller.pending.inspect(item.pending_id) == item
        session._active = None
    finally:
        session._active = None
        session.close().result(timeout=5)


def test_remove_uses_canonical_control_and_preserves_other_pending_items(tmp_path):
    session = create_session(tmp_path)
    bridge = WorkbenchBridge(session=session)
    try:
        first = session.controller.pending.add(build_natural_envelope("Remove only this"))
        second = session.controller.pending.add(build_natural_envelope("Keep this"))
        result = bridge.control(f"/pending discard {first.pending_id}")
        assert result["accepted"] is True
        assert result["observation"]["status"] == "succeeded"
        assert bridge.task_status()["pending_items"] == [{"pending_id": second.pending_id, "text": second.visible_text}]
        assert session._runtime is None
    finally:
        bridge.close()
