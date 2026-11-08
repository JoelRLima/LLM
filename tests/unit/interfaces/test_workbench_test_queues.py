"""Workbench test batteries reuse the shared worker, queue service and authority."""
from __future__ import annotations

import json
import time
from threading import Event, Thread

import pytest

from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.application import test_queue_runtime as runtime
from llm_agent.application.context import AppPaths
from llm_agent.application.task_execution import TaskExecutionStart
from llm_agent.application.test_queue_service import QueueExecutionRequest, TestQueueService
from llm_agent.interactive.session import InteractiveSession
from llm_agent.interfaces.workbench import _bridge_test_queues
from llm_agent.interfaces.workbench.webview import WorkbenchBridge


@pytest.fixture
def battery(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    paths = AppPaths.discover(tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    tasks = tmp_path / "tasks"
    for name in ["01-first", "02-next"]:
        case = tasks / name
        case.mkdir(parents=True)
        (case / "task.md").write_text("Create result.txt containing done")
        (case / "checks.json").write_text(json.dumps({"checks": [{"kind": "text_equals", "path": "result.txt", "expected": "done"}]}))
    session = InteractiveSession(TaskExecutionStart(workspace, paths, configure_logging=False))
    bridge = WorkbenchBridge(session=session)
    state = bridge.test_queue_select(session.workspace.workspace_id, "create", str(tasks))
    assert state["accepted"], state
    yield session, bridge, tasks, state
    session.close().result(timeout=10)


def options(**changes):
    return {"profile": "local_8gb", "live_authorized": True, "automatic_approval": False,
            "case_seconds": 300, "total_seconds": 3600, "skip_interrupted": False, **changes}


def action(battery, name, value="", settings=None, state=None):
    session, bridge, _, initial = battery
    selected = state or initial
    return bridge.test_queue_action(session.workspace.workspace_id, selected["queue_id"], selected["queue"]["manifest_hash"], name, value, settings)


def start(battery, settings=None):
    session, bridge, _, state = battery
    checked = action(battery, "check", settings=settings or options())
    assert checked["accepted"], checked
    return bridge.test_queue_start(session.workspace.workspace_id, state["queue_id"], state["queue"]["manifest_hash"], settings or options())


def settle(bridge):
    deadline = time.monotonic() + 10
    events = []
    while time.monotonic() < deadline:
        events.extend(bridge.wait_events(50))
        if any(item["type"] in {"settled", "error"} for item in events):
            return events
    pytest.fail("battery did not settle")


def successful(case, workspace, cancellation, seconds):
    (workspace / "result.txt").write_text("done")
    return {"status": "completed", "success": True}


def test_preparation_and_preflight_never_spawn_or_call_model(battery, monkeypatch):
    import subprocess

    import requests
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **k: pytest.fail("model contacted"))
    session, bridge, _, state = battery
    assert len(state["queue"]["cases"]) == 2
    checked = action(battery, "check", settings=options(live_authorized=False))
    assert checked["accepted"] and checked["preflight"]["configuration"]["resume_compatible"]
    assert session._runtime is None
    assert bridge.test_queue_state(session.workspace.workspace_id)["queue_id"] == state["queue_id"]


def test_detached_start_uses_same_review_and_explicit_model_gate(battery, monkeypatch):
    calls = []
    monkeypatch.setattr(TestQueueService, "start_detached", lambda self, request: calls.append(request) or {"status": "starting", "pid": 123})
    assert not action(battery, "detach", settings=options())["accepted"]
    assert action(battery, "check", settings=options())["accepted"]
    result = action(battery, "detach", settings=options())
    assert result["accepted"] and result["worker"]["status"] == "starting"
    assert len(calls) == 1 and calls[0].expected_identity
    assert calls[0].live_authorized is True
    assert battery[0]._runtime is None
    assert not action(battery, "detach", settings=options())["accepted"]
    assert action(battery, "check", settings=options())["accepted"]
    battery[0].set_llm_enabled(False)
    assert not action(battery, "detach", settings=options())["accepted"]
    assert len(calls) == 1


def test_shared_execution_settles_and_exposes_independent_evidence(battery, monkeypatch):
    monkeypatch.setattr(runtime, "RuntimeExecutor", lambda *a, **k: successful)
    session, bridge, _, _ = battery
    assert start(battery)["accepted"]
    assert any(event["type"] == "settled" for event in settle(bridge))
    state = bridge.test_queue_state(session.workspace.workspace_id)
    assert state["queue"]["counts"] == {"verified": 2}
    assert state["queue"]["execution_options"]["profile"] == "local_8gb"
    detail = action(battery, "details", "01-first")
    assert detail["details"]["verification"]["status"] == "passed"
    exported = bridge.test_queue_export(session.workspace.workspace_id, state["queue_id"], state["queue"]["manifest_hash"])
    assert exported["accepted"] and exported["report_path"].endswith("report.html")
    assert session._runtime is None  # No second chat runtime was constructed.


def test_pause_at_case_boundary_and_resume_without_repeating(battery, monkeypatch):
    entered, release = Event(), Event()
    seen = []
    def execute(case, *args):
        seen.append(case.case_id)
        entered.set()
        assert release.wait(5)
        return successful(case, *args)
    monkeypatch.setattr(runtime, "RuntimeExecutor", lambda *a, **k: execute)
    session, bridge, _, _ = battery
    assert start(battery)["accepted"]
    assert entered.wait(3)
    assert action(battery, "pause")["accepted"]
    assert not bridge.submit("another task")["accepted"]
    assert not bridge.apply_update()["accepted"]
    release.set()
    settle(bridge)
    state = bridge.test_queue_state(session.workspace.workspace_id)
    assert state["queue"]["status"] == "paused"
    assert state["queue"]["counts"] == {"verified": 1, "pending": 1}
    assert start(battery)["accepted"]
    settle(bridge)
    assert seen == ["01-first", "02-next"]


def test_stop_reaches_canonical_cancellation_and_preserves_pending(battery, monkeypatch):
    entered = Event()
    def execute(case, workspace, cancellation, seconds):
        entered.set()
        deadline = time.monotonic() + 5
        while not cancellation.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert cancellation.is_set()
        return {"status": "interrupted", "success": False}
    monkeypatch.setattr(runtime, "RuntimeExecutor", lambda *a, **k: execute)
    session, bridge, _, _ = battery
    assert start(battery)["accepted"]
    assert entered.wait(3)
    assert action(battery, "stop")["accepted"]
    settle(bridge)
    state = bridge.test_queue_state(session.workspace.workspace_id)
    assert state["queue"]["counts"] == {"interrupted": 1, "pending": 1}


def test_recent_queue_is_restored_by_a_new_session(battery):
    session, _, _, state = battery
    other = InteractiveSession(session.start)
    try:
        restored = other.test_queue_state(other.workspace.workspace_id)
        assert restored["queue_id"] == state["queue_id"]
        assert restored["queue"]["manifest_hash"] == state["queue"]["manifest_hash"]
    finally:
        other.close().result(5)


def test_context_and_manifest_fences_reject_stale_actions(battery):
    session, bridge, _, state = battery
    assert not bridge.test_queue_select("old-workspace", "select", state["queue_id"])["accepted"]
    assert not bridge.test_queue_action(session.workspace.workspace_id, state["queue_id"], "0" * 64, "check", "", options())["accepted"]
    assert not bridge.test_queue_start(session.workspace.workspace_id, "0" * 64, state["queue"]["manifest_hash"], options())["accepted"]


def test_native_picker_cancellation_and_invalid_actions_do_not_change_selection(battery, monkeypatch):
    session, bridge, _, state = battery
    monkeypatch.setattr(_bridge_test_queues, "choose_directory", lambda: {"path": None, "cancelled": True})
    assert bridge.test_queue_pick_directory(session.workspace.workspace_id)["cancelled"]
    assert not action(battery, "export", "<script>bad()</script>")["accepted"]
    assert bridge.test_queue_state(session.workspace.workspace_id)["queue_id"] == state["queue_id"]


def test_missing_live_authority_cannot_be_bypassed_from_desktop(battery, monkeypatch):
    monkeypatch.setattr(runtime, "RuntimeExecutor", lambda *a, **k: pytest.fail("authority bypassed"))
    assert start(battery, options(live_authorized=False))["accepted"]
    assert any(item["type"] == "error" for item in settle(battery[1]))
    assert battery[1].test_queue_state(battery[0].workspace.workspace_id)["queue"]["counts"] == {"pending": 2}


def test_pause_before_queue_claim_is_not_lost(battery, monkeypatch):
    preparing, release = Event(), Event()
    original = runtime.prepare_config
    def prepare(*args):
        if battery[0]._test_queues.running:
            preparing.set()
            assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(runtime, "prepare_config", prepare)
    monkeypatch.setattr(runtime, "RuntimeExecutor", lambda *a, **k: successful)
    session, bridge, _, _ = battery
    assert start(battery)["accepted"]
    assert preparing.wait(3)
    assert action(battery, "pause")["accepted"]
    release.set()
    settle(bridge)
    state = bridge.test_queue_state(session.workspace.workspace_id)
    assert state["queue"]["status"] == "paused"
    assert state["queue"]["counts"] == {"pending": 2}


def test_pending_chat_is_preserved_and_prevents_starting_battery(battery):
    session, bridge, _, state = battery
    from llm_agent.interactive.admission import build_natural_envelope
    session.controller.pending.add(build_natural_envelope("Preserve my next request"))
    assert not start(battery)["accepted"]
    assert len(session.controller.pending.list()) == 1


def test_desktop_can_pause_a_queue_owned_by_another_caller(battery, monkeypatch):
    entered, release = Event(), Event()
    def execute(case, *args):
        entered.set()
        assert release.wait(5)
        return successful(case, *args)
    monkeypatch.setattr(runtime, "RuntimeExecutor", lambda *a, **k: execute)
    session, bridge, _, state = battery
    service = TestQueueService(session._test_queues.selected.root)
    results = []
    worker = Thread(target=lambda: results.append(service.run(QueueExecutionRequest(
        session.start.app_paths.config_file, profile="local_8gb", live_authorized=True))))
    worker.start()
    try:
        assert entered.wait(3)
        observed = bridge.test_queue_state(session.workspace.workspace_id)
        assert observed["queue"]["runner_active"] and not observed["run_active"]
        assert action(battery, "pause")["accepted"]
    finally:
        release.set()
        worker.join(8)
    assert not worker.is_alive()
    assert results[0].document["status"] == "paused"
    assert results[0].document["counts"] == {"verified": 1, "pending": 1}


def test_desktop_start_requires_preflight_in_shared_session(battery):
    session, bridge, _, state = battery
    reply = bridge.test_queue_start(session.workspace.workspace_id, state["queue_id"], state["queue"]["manifest_hash"], options())
    assert not reply["accepted"]
    assert "preparacao" in reply["message"]


def test_configuration_drift_after_preflight_never_reaches_executor(battery, monkeypatch):
    session, bridge, _, state = battery
    assert action(battery, "check", settings=options())["accepted"]
    ConfigRepository(session.start.app_paths).update({"max_model_calls": 1})
    monkeypatch.setattr(runtime, "RuntimeExecutor", lambda *a, **k: pytest.fail("changed settings reached executor"))
    assert bridge.test_queue_start(session.workspace.workspace_id, state["queue_id"], state["queue"]["manifest_hash"], options())["accepted"]
    assert any(event["type"] == "error" for event in settle(bridge))
    assert bridge.test_queue_state(session.workspace.workspace_id)["queue"]["counts"] == {"pending": 2}
