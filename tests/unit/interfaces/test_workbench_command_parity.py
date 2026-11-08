from __future__ import annotations

from concurrent.futures import Future
from threading import Event

import pytest

from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.application.approval import ApprovalPrompt
from llm_agent.application.task_execution import TaskExecutionStart
from llm_agent.interactive import session_llm, session_navigation
from llm_agent.interactive._session_routing import QUERY_ACTIONS
from llm_agent.interactive.commands import DEFAULT_COMMAND_REGISTRY
from llm_agent.interactive.controller_models import SubmissionEnvelope
from llm_agent.interactive.local_commands import COMMAND_ACTION_IDS
from llm_agent.interactive.session import InteractiveSession
from llm_agent.interactive.session_controls import CONTROL_ACTION_IDS
from llm_agent.interfaces.workbench.webview import WorkbenchBridge
from llm_agent.workspace.paths import AppPaths


@pytest.fixture
def local_session(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = AppPaths.discover(tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    session = InteractiveSession(TaskExecutionStart(workspace, paths, configure_logging=False))
    yield session
    if not session._closed:
        session.close().result(timeout=10)


def _submit(session: InteractiveSession, text: str) -> dict:
    observation, _ = session.submit(text).result(timeout=10)
    return observation


def test_real_runtime_local_commands_match_shared_cli_capabilities(local_session, tmp_path):
    session = local_session
    # Starting the normal runtime and exercising local use cases does not call a model.
    memory_path = tmp_path / "memory.json"
    history_path = tmp_path / "history.json"
    assert _submit(session, "/memory remember project parity-check")["status"] == "succeeded"
    assert "parity-check" in _submit(session, "/memory show")["answer"]
    # key_findings are intentionally SQLite-owned and are not serialized into the
    # legacy JSON snapshot; exercise the documented JSON-backed notes section too.
    session._runtime._owner.orchestrator.remember("note-key", "note-value", section="notes")
    assert _submit(session, f"/memory save {memory_path}")["status"] == "succeeded"
    _submit(session, "/memory clear")
    assert _submit(session, f"/memory load {memory_path}")["status"] == "succeeded"
    assert "note-key" in _submit(session, "/memory show")["answer"]
    _submit(session, "/memory forget project")

    _submit(session, "/system set parity system prompt")
    assert "parity system prompt" in _submit(session, "/system show")["answer"]
    _submit(session, "/think 512")
    assert session._runtime._conversation is not None
    from llm_agent.application.conversation import read_conversation
    assert read_conversation(session._runtime._conversation).thinking_budget == 512
    _submit(session, "/think")
    assert read_conversation(session._runtime._conversation).thinking_budget == 0

    assert _submit(session, "/history save " + str(history_path))["status"] == "succeeded"
    _submit(session, "/history clear")
    assert _submit(session, "/history load " + str(history_path))["status"] == "succeeded"
    for mode in ("editor", "full", "read-only"):
        outcome = _submit(session, f"/mode {mode}")
        assert outcome["status"] == "succeeded", outcome
        observed = "-".join(session.execution_mode().casefold().replace("_", " ").split())
        assert observed == mode


def test_every_catalog_action_has_an_explicit_session_route():
    agent_actions = {
        "interaction.agent_submit", "interaction.code_submit", "query.web_search", "run.retry",
    }
    special_actions = {"discovery.help", "discovery.commands", "session.exit"} | session_llm.ACTIONS
    implemented = (
        QUERY_ACTIONS.keys()
        | CONTROL_ACTION_IDS
        | COMMAND_ACTION_IDS
        | session_navigation.ACTIONS
        | agent_actions
        | special_actions
        | {"process.exec"}
    )
    catalog = {binding.action_id for binding in DEFAULT_COMMAND_REGISTRY._bindings}
    assert catalog == implemented


def test_active_approval_wait_is_cancelled_and_bridge_rejects_stale_identity(local_session):
    session = local_session
    published = Event()
    started = Event()
    prompt = ApprovalPrompt("write", "workspace/file.txt", "apply change", {"task_id": "task"})

    def operation(envelope, _cancel, register):
        session.approval_broker.bind_generation(envelope.run_generation, on_attention=lambda _item: published.set())
        register(lambda: session.approval_broker.invalidate(generation=envelope.run_generation))
        started.set()
        response = session.approval_broker.present(prompt)
        return {"status": "cancelled", "answer": response.value}

    pending = session._controlled(operation, owner="approval-test")
    assert started.wait(2) and published.wait(2)
    current = session.approval_broker.current()
    assert current is not None
    bridge = WorkbenchBridge(session=session)
    stale = bridge.resolve_attention(
        current.identity.attention_id, "approve", current.identity.run_generation + 1,
        current.identity.request_fingerprint,
    )
    assert stale["accepted"] is False
    assert session.approval_broker.current() is current
    session.cancel()
    observation = pending.result(timeout=5)
    assert observation["status"] == "cancelled"
    assert session.approval_broker.current() is None


def test_pending_send_consumes_once_and_failed_send_preserves_item(local_session, monkeypatch):
    session = local_session
    envelope = SubmissionEnvelope(0, "follow up", "natural_text", "AGENTIC", "AGENTIC_SUBMIT",
                                  "PENDING_EXACT_TEXT", "follow up", "natural", "test")
    item = session.controller.pending.add(envelope)
    calls: list[str] = []

    def accepted(text):
        calls.append(text)
        done: Future = Future()
        done.set_result(({"status": "succeeded", "answer": "sent"}, None))
        return done

    monkeypatch.setattr(session, "submit", accepted)
    session.send_pending(item.pending_id).result(timeout=1)
    with pytest.raises(ValueError, match="pending item not found"):
        session.send_pending(item.pending_id)
    assert calls == ["follow up"]

    failed = session.controller.pending.add(envelope)
    monkeypatch.setattr(session, "submit", lambda _text: (_ for _ in ()).throw(RuntimeError("admission failed")))
    with pytest.raises(RuntimeError, match="admission failed"):
        session.send_pending(failed.pending_id)
    assert session.controller.pending.inspect(failed.pending_id) is not None


def test_doctor_without_configuration_never_bootstraps_runtime(tmp_path, monkeypatch):
    session = InteractiveSession(TaskExecutionStart(tmp_path, AppPaths.discover(tmp_path / "home", env={})))
    monkeypatch.setattr(session, "_ensure_runtime", lambda: pytest.fail("doctor started runtime"))
    try:
        result = _submit(session, "/doctor")
        assert result["answer"]
        assert session._runtime is None
    finally:
        session.close().result(5)


def test_workspace_transition_rejects_previous_references_and_resets_mode(local_session, tmp_path):
    session = local_session
    (session.workspace.root / "old.txt").write_text("old workspace", encoding="utf-8")
    _, reference = session.submit("/read old.txt").result(5)
    _submit(session, "/mode editor")
    next_workspace = tmp_path / "another project"
    next_workspace.mkdir()
    (next_workspace / "new.txt").write_text("new workspace", encoding="utf-8")
    result = _submit(session, f'/workspace switch "{next_workspace}"')
    assert result["session_changed"] is True
    assert session.workspace.root == next_workspace.resolve()
    assert session.execution_mode() == "READ_ONLY"
    with pytest.raises(ValueError):
        session.read(reference).result(5)
    _, current = session.submit("/read new.txt").result(5)
    assert "new workspace" in session.read(current).result(5).text
    assert _submit(session, "/model")["model"]


def test_pending_edit_returns_draft_without_consuming_and_closed_session_rejects(local_session):
    session = local_session
    item = session.controller.pending.add(SubmissionEnvelope(0, "follow up", "natural_text", "AGENTIC", "AGENTIC_SUBMIT",
        "PENDING_EXACT_TEXT", "follow up", "natural", "test"))
    observation = _submit(session, f"/pending edit {item.pending_id}")
    assert observation["draft"] == "follow up"
    assert session.controller.pending.inspect(item.pending_id) is not None
    session.close().result(5)
    with pytest.raises(RuntimeError, match="closed"):
        session.submit("/status")


def test_code_help_uses_shared_workflow_without_inference(local_session):
    observation, reference = local_session.submit("/code help").result(5)
    assert observation["status"] == "succeeded"
    assert reference is not None
    content = local_session.read(reference).result(5)
    assert "/code" in content.text


def test_cli_memory_preserves_multiword_values_using_same_service(local_session):
    from types import SimpleNamespace

    from llm_agent.application.task_execution import read_execution_context
    from llm_agent.interfaces.cli.memory_commands import remember

    runtime = local_session._ensure_runtime()
    context = read_execution_context(runtime)
    cli = SimpleNamespace(task_execution=runtime, conversation=context.conversation, config=context.config)
    remember("/remember project several words preserved", cli)
    assert "several words preserved" in _submit(local_session, "/memory show")["answer"]
