from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from time import monotonic

import pytest

from llm_agent.agent.application import AgentApplication
from llm_agent.agent.llm.contracts import ProviderCapabilities
from llm_agent.agent.llm.errors import ModelConnectionError
from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.agent.tools.authority import OperationalMode
from llm_agent.application.task_execution import TaskExecutionStart, _retain_runtime
from llm_agent.interfaces.workbench.webview import WorkbenchBridge
from llm_agent.workspace.paths import AppPaths
from tests.integration.test_agent_application_evaluation import JourneyGateway


@pytest.fixture
def tmp_path():
    repository = Path(__file__).resolve().parents[2]
    root = repository / (".workbench-f1-test-" + uuid.uuid4().hex)
    root.mkdir()
    try:
        yield root
    finally:
        resolved = root.resolve()
        if resolved.parent != repository:
            raise AssertionError("test scratch path escaped the repository root")
        shutil.rmtree(resolved)


def _install_gateway(monkeypatch, gateway):
    from llm_agent.application.approval import _bind_approval_presenter
    from llm_agent.interactive import session as session_module

    def start(request: TaskExecutionStart, approval):
        owner = AgentApplication.create(
            paths=request.app_paths,
            workspace=request.workspace,
            gateway=gateway,
            approval_policy=_bind_approval_presenter(approval),
            operational_mode=OperationalMode.READ_ONLY,
            configure_logging=False,
        )
        return _retain_runtime(owner)

    monkeypatch.setattr(session_module, "start_interactive_session", start)


def _drain_until_terminal(bridge: WorkbenchBridge, request_id: str, timeout: float = 30.0):
    deadline = monotonic() + timeout
    events = []
    while monotonic() < deadline:
        batch = bridge.wait_events(1000)
        events.extend(batch)
        if any(
            event.get("request_id") == request_id and event["type"] in {"settled", "error"}
            for event in batch
        ):
            return events
    raise AssertionError(f"request {request_id} did not settle; events={events!r}")


def test_installed_bridge_full_read_only_flow_uses_shared_services(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("CAP_READ_EVIDENCE\n", encoding="utf-8")
    home = tmp_path / "agent-home"
    paths = AppPaths.discover(home, env={})
    ConfigRepository(paths).initialize()

    objective = "CAP_READ: leia notes.txt e informe CAP_READ_EVIDENCE."
    gateway = JourneyGateway(objective)
    gateway.capabilities = ProviderCapabilities(streaming=True)
    _install_gateway(monkeypatch, gateway)
    bridge = WorkbenchBridge(workspace=workspace, home=home)

    try:
        info = bridge.initialize()
        assert info["mode"] == "READ_ONLY"
        assert info["profile"]["model"]
        assert info["availability"]["backend"]["connectivity"] == "unverified"

        listing = bridge.query_workspace("list_files", {"path": "."})
        assert listing["accepted"] is True
        listing_content = bridge.read_report(listing["reference"]["token"])
        assert "file notes.txt" in listing_content["text"]
        assert listing_content["reference"]["workspace_id"] == info["workspace_id"]

        read = bridge.query_workspace("read", {"file_path": "notes.txt"})
        assert read["accepted"] is True
        assert "CAP_READ_EVIDENCE" in bridge.read_report(read["reference"]["token"])["text"]

        assert bridge.initialize()["llm_enabled"] is False
        assert not gateway.calls
        assert bridge.set_llm_enabled(True, info["workspace_id"])["accepted"]
        assert not gateway.calls
        first = bridge.submit(objective)
        assert first["accepted"] is True
        first_events = _drain_until_terminal(bridge, first["request_id"])
        settled = next(event for event in first_events if event["type"] == "settled")
        assert any(event["type"] == "delta" for event in first_events)
        assert settled["observation"]["status"] == "succeeded"
        assert settled["reference"] is not None
        report = bridge.read_report(settled["reference"]["token"])
        report_json = json.loads(report["text"])
        assert report_json["run_id"] == settled["reference"]["run_id"]
        assert report_json["steps"][0]["tool"] == "file_reader"
        assert "CAP_READ_EVIDENCE" in report_json["steps"][0]["result"]["data_summary"]

        tools_request = bridge.inspect("tools")
        assert tools_request["accepted"] is True
        tools_events = _drain_until_terminal(bridge, tools_request["request_id"])
        tools_result = next(event for event in tools_events if event["type"] == "settled")
        inventory = bridge.read_report(tools_result["reference"]["token"])
        assert "file_reader" in inventory["text"]
        assert "capabilities=" in inventory["text"]

        calls_before_second_run = len(gateway.calls)
        second = bridge.submit(objective)
        assert second["accepted"] is True
        second_events = _drain_until_terminal(bridge, second["request_id"])
        assert next(event for event in second_events if event["type"] == "settled")["observation"]["status"] == "succeeded"
        assert len(gateway.calls) > calls_before_second_run
    finally:
        bridge.close()


def test_missing_profile_keeps_local_files_and_tasks_available(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "offline.txt").write_text("local data\n", encoding="utf-8")
    home = tmp_path / "empty-agent-home"

    def no_network(*_args, **_kwargs):
        pytest.fail("connection test tried network without a profile")

    monkeypatch.setattr("llm_agent.interactive.availability.urlopen", no_network)
    bridge = WorkbenchBridge(workspace=workspace, home=home)
    try:
        info = bridge.initialize()
        assert info["profile"] is None
        assert info["availability"]["backend"]["state"] == "not_configured"

        listing = bridge.query_workspace("list_files", {"path": "."})
        assert listing["accepted"] is True
        assert "offline.txt" in bridge.read_report(listing["reference"]["token"])["text"]
        read = bridge.query_workspace("read", {"file_path": "offline.txt"})
        assert "local data" in bridge.read_report(read["reference"]["token"])["text"]

        tasks = bridge.tasks()
        assert tasks["current"]["mode"] == "READ_ONLY"
        assert tasks["runs"] == []
        assert bridge.task_status()["workspace_id"] == info["workspace_id"]
        assert bridge.test_connection()["backend"]["state"] == "not_configured"
        assert bridge.submit("/agent resuma o objetivo")["accepted"] is False
        with pytest.raises(ValueError, match="Referência de artefato indisponível"):
            bridge.read_report("missing-report")
    finally:
        bridge.close()


def test_causal_connection_failure_blocks_repeat_without_gateway_retry(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "agent-home"
    paths = AppPaths.discover(home, env={})
    ConfigRepository(paths).initialize()

    gateway = JourneyGateway("offline transport")

    def refused(request):
        gateway.calls.append(request)
        raise ModelConnectionError("synthetic connection refused")

    gateway.complete = refused
    _install_gateway(monkeypatch, gateway)
    bridge = WorkbenchBridge(workspace=workspace, home=home)
    try:
        info = bridge.initialize()
        assert info["llm_enabled"] is False
        assert bridge.set_llm_enabled(True, info["workspace_id"])["accepted"]
        assert not gateway.calls
        first = bridge.submit("responda naturalmente")
        assert first["accepted"] is True
        first_events = _drain_until_terminal(bridge, first["request_id"])
        terminal = next(event for event in first_events if event["type"] in {"settled", "error"})
        assert terminal["type"] == "settled"
        assert terminal["observation"]["reason_code"] == "INTERACTION_RESOLVER_UNAVAILABLE"
        assert terminal["observation"]["model_transport_failure"] == "MODEL_CONNECTION_ERROR"
        assert bridge.availability()["backend"]["state"] == "unavailable"

        second = bridge.submit("tente outra vez automaticamente")
        assert second["accepted"] is False
        assert "BACKEND_UNAVAILABLE" in second["message"]
        assert len(gateway.calls) == 1
    finally:
        bridge.close()
