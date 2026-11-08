"""The local workbench remains useful with no config and forbidden model access."""
from __future__ import annotations

from threading import Event
from types import SimpleNamespace

import pytest
import requests

from llm_agent.agent.llm.contracts import UnavailableModelGateway
from llm_agent.agent.llm.errors import ModelConnectionError
from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.application import code_commands
from llm_agent.application.context import AppPaths
from llm_agent.application.local_code import execute_local_code_command, is_local_code_command
from llm_agent.application.task_execution import TaskExecutionStart
from llm_agent.interactive import availability
from llm_agent.interactive import session as session_module
from llm_agent.interactive.session import InteractiveSession
from llm_agent.interactive.worker_operations import execute_code
from llm_agent.interfaces.workbench.webview import WorkbenchBridge


@pytest.fixture
def local_session(tmp_path, monkeypatch):
    def never(*args, **kwargs):
        pytest.fail("local operation attempted model access or Agent startup")
    monkeypatch.setattr(requests.sessions.Session, "request", never)
    monkeypatch.setattr(availability, "urlopen", never)
    monkeypatch.setattr(session_module, "start_interactive_session", never)
    monkeypatch.setattr(UnavailableModelGateway, "complete", never)
    monkeypatch.setattr(UnavailableModelGateway, "stream", never)
    monkeypatch.setattr(code_commands, "_resolve_gateway", never)
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "sample.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    paths = AppPaths.discover(tmp_path / "home", env={})
    session = InteractiveSession(TaskExecutionStart(workspace, paths, configure_logging=False))
    yield session
    assert session._runtime is None
    session.close().result(5)


@pytest.mark.parametrize("command", ["/code help", "/code analyze sample.py", "/code review sample.py"])
def test_local_code_uses_same_engine_without_configuration_or_gateway(local_session, command):
    before = (local_session.workspace.root / "sample.py").read_bytes()
    observation, reference = local_session.submit(command).result(5)
    assert observation["status"] == "succeeded", observation
    assert observation["model_used"] is False
    assert reference
    assert local_session.read(reference).result(5).text
    assert (local_session.workspace.root / "sample.py").read_bytes() == before
    assert not local_session.start.app_paths.config_file.exists()


def test_cli_code_adapter_uses_shared_local_use_case(local_session):
    context = SimpleNamespace(workspace=local_session.workspace)
    envelope = SimpleNamespace(visible_text="/code analyze sample.py")
    outcome = execute_code(context, envelope, Event(), lambda callback: None,
        executor=lambda *a, **k: pytest.fail("model path used"),
        capabilities=lambda *a: pytest.fail("runtime required"))
    assert outcome.status == "succeeded"


def test_local_analysis_still_works_after_model_transport_failure(local_session):
    ConfigRepository(local_session.start.app_paths).initialize()
    local_session.availability.refresh(local_session.start)
    local_session.availability.failure(ModelConnectionError("server unavailable"))
    assert local_session.submit("/code analyze sample.py").result(5)[0]["status"] == "succeeded"
    assert local_session.availability.value.state.value == "unavailable"


@pytest.mark.parametrize("command", ["/ls", "/read sample.py", "/find return", "/help", "/commands", "/status", "/workspace", "/inspect"])
def test_existing_local_commands_work_with_no_model_config(local_session, command):
    observation, _ = local_session.submit(command).result(5)
    assert observation["status"] == "succeeded", observation


def test_workspace_picker_and_switch_do_not_require_model_config(local_session, tmp_path):
    bridge = WorkbenchBridge(session=local_session)
    options = bridge.navigation_options()
    assert options["accepted"]
    assert options["profiles"] == []
    assert options["configuration_error"]
    other = tmp_path / "another project"
    other.mkdir()
    observation, _ = local_session.submit(f"/workspace switch {other}").result(5)
    assert observation["status"] == "succeeded"
    assert local_session.workspace.root == other.resolve()


def test_test_queue_structure_and_results_are_local_without_profile(local_session, tmp_path):
    tasks = tmp_path / "tasks"
    case = tasks / "01-local"
    case.mkdir(parents=True)
    (case / "task.md").write_text("Create result.txt")
    bridge = WorkbenchBridge(session=local_session)
    selected = bridge.test_queue_select(local_session.workspace.workspace_id, "create", str(tasks))
    assert selected["accepted"] and selected["profiles"] == []
    args = (local_session.workspace.workspace_id, selected["queue_id"], selected["queue"]["manifest_hash"])
    checked = bridge.test_queue_action(*args, "check", "", {})
    assert checked["accepted"] and checked["local_only"] is True
    assert checked["preflight"]["without_checks"] == ["01-local"]
    assert "configuration" not in checked["preflight"]
    assert bridge.test_queue_action(*args, "details", "01-local")["accepted"]
    assert bridge.test_queue_export(*args)["accepted"]


def test_plugin_catalog_is_available_without_llm(local_session):
    state = local_session.plugin_state()
    assert isinstance(state["items"], list)


@pytest.mark.parametrize("command", ["/code modify sample.py -- change it", "/code generate a.py -- new file", "/agent do something"])
def test_model_required_operations_do_not_fall_back_to_local_execution(local_session, command):
    assert not is_local_code_command(command)
    with pytest.raises(ValueError, match="BACKEND_NOT_CONFIGURED"):
        local_session.submit(command)


def test_local_code_service_refuses_write_requests(local_session):
    with pytest.raises(ValueError, match="exige"):
        execute_local_code_command("/code modify sample.py -- change it", local_session.workspace.root)


def test_explicit_model_activation_never_probes_and_local_mode_is_enforced(local_session):
    session = local_session
    session.set_llm_enabled(False)
    assert session.presentation_snapshot()["llm_enabled"] is False
    for text in ("/agent do something", "/code modify sample.py -- change it"):
        with pytest.raises(ValueError, match="LLM_DISABLED"):
            session.submit(text)
    assert session.submit("/code analyze sample.py").result(5)[0]["status"] == "succeeded"
    with pytest.raises(ValueError, match="BACKEND_NOT_CONFIGURED"):
        session.set_llm_enabled(True)
    assert not session.llm_enabled
    ConfigRepository(session.start.app_paths).initialize()
    assert session.set_llm_enabled(True)["llm_enabled"] is True
    assert session._runtime is None
    assert session.set_llm_enabled(False)["llm_enabled"] is False
    with pytest.raises(ValueError, match="LLM_DISABLED"):
        session.test_queue_start("irrelevant", "irrelevant", "irrelevant", {})


@pytest.mark.parametrize("enabled", ["false", 1, None, []])
def test_model_activation_rejects_non_boolean_values(local_session, enabled):
    with pytest.raises(ValueError, match="boolean"):
        local_session.set_llm_enabled(enabled)


def test_bridge_model_activation_checks_workspace_busy_and_closed(local_session):
    session = local_session
    bridge = WorkbenchBridge(session=session)
    workspace = session.workspace.workspace_id
    assert not bridge.set_llm_enabled(False, "wrong")["accepted"]
    assert bridge.set_llm_enabled(False, workspace)["accepted"]
    bridge._active_request_id = "running"
    assert not bridge.set_llm_enabled(True, workspace)["accepted"]
    bridge._active_request_id = None
    bridge._closed = True
    assert not bridge.set_llm_enabled(True, workspace)["accepted"]
    assert not session.llm_enabled


def test_workbench_composition_starts_with_local_mode(tmp_path):
    from llm_agent.interfaces.workbench.webview import create_session
    session, _ = create_session(workspace=tmp_path, home=tmp_path / "home")
    try:
        assert session.llm_enabled is False
        with pytest.raises(ValueError, match="LLM_DISABLED"):
            session.submit("/agent do something")
    finally:
        session.close().result(5)


def test_core_model_activation_cannot_change_during_active_local_operation(local_session):
    release = Event()
    future = local_session._controlled(lambda *_: release.wait(5), track_activity=False)
    try:
        with pytest.raises(RuntimeError, match="processing"):
            local_session.set_llm_enabled(False)
    finally:
        release.set()
        future.result(5)
    assert local_session.set_llm_enabled(False)["llm_enabled"] is False
