from __future__ import annotations

import json
from pathlib import Path
from time import monotonic

import pytest

from llm_agent.agent.application import AgentApplication
from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.agent.tools.authority import OperationalMode
from llm_agent.application.task_execution import TaskExecutionStart, _retain_runtime
from llm_agent.interfaces.workbench import session as session_module
from llm_agent.interfaces.workbench.session import UnavailableApproval, WorkbenchSession
from llm_agent.workspace.paths import AppPaths
from tests.integration.test_agent_application_evaluation import JourneyGateway


@pytest.fixture
def real_session(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("CAP_READ_EVIDENCE\n", encoding="utf-8")
    paths = AppPaths.discover(tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    objective = "CAP_READ: leia notes.txt e informe CAP_READ_EVIDENCE."
    gateway = JourneyGateway(objective)

    def start(request, approval):
        from llm_agent.application.approval import _bind_approval_presenter
        return _retain_runtime(AgentApplication.create(
            paths=request.app_paths, workspace=request.workspace, gateway=gateway,
            approval_policy=_bind_approval_presenter(approval),
            operational_mode=OperationalMode.READ_ONLY, configure_logging=False,
        ))

    monkeypatch.setattr(session_module, "start_interactive_session", start)
    session = WorkbenchSession(TaskExecutionStart(workspace, paths))
    yield session, objective, workspace, gateway
    if not session._closed:
        session.close().result(timeout=15)


def test_real_analysis_produces_original_report_and_keeps_canonical_identity(real_session):
    session, objective, workspace, gateway = real_session
    observation, reference = session.submit(objective).result(timeout=30)
    assert observation["status"] == "succeeded", observation
    assert reference is not None
    content = session.read(reference).result(timeout=10)
    report = json.loads(content.text)
    assert report["run_id"] == reference.run_id == observation["run_id"]
    assert reference.completeness == "final"
    assert reference.source == "TaskReportBuilder"
    assert report["steps"][0]["tool"] == "file_reader"
    assert report["steps"][0]["result"]["present"]
    assert "CAP_READ_EVIDENCE" in report["steps"][0]["result"]["data_summary"]
    assert Path(observation["report_path"]).read_text(encoding="utf-8") == content.text
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "CAP_READ_EVIDENCE\n"
    assert gateway.calls
    inventory = session.inventory().result(timeout=10)
    assert "file_reader" in inventory.text
    assert inventory.reference.source == "ToolRegistry / WorkspaceExtensionService"


def test_agent_response_stream_is_forwarded_from_the_session_worker(real_session):
    from llm_agent.agent.llm.contracts import ProviderCapabilities

    session, objective, _, gateway = real_session
    gateway.capabilities = ProviderCapabilities(streaming=True)
    chunks: list[str] = []
    observation, _ = session.submit(objective, stream_callback=chunks.append).result(timeout=30)
    assert observation["status"] == "succeeded", observation
    assert chunks
    assert "".join(chunks) == observation.get("answer")


def test_native_chat_retains_conversation_when_report_tab_is_opened_closed_and_reopened(real_session):
    tk = pytest.importorskip("tkinter", reason="optional Tk desktop module unavailable")
    from llm_agent.interfaces.workbench.window import WorkbenchWindow
    session, objective, _, _ = real_session
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk desktop display unavailable")
    root.withdraw()
    window = WorkbenchWindow(root, session)

    def wait():
        deadline = monotonic() + 30
        while window._pending is not None and monotonic() < deadline:
            root.update()
        assert window._pending is None

    try:
        window.prompt.insert("1.0", objective)
        window.submit()
        wait()
        assert len(window.offers.get_children()) == 1
        before = window.transcript.get("1.0", "end")
        assert objective in before
        window.open_selected()
        wait()
        assert len(window.panels.tabs.tabs()) == 1
        reference = next(iter(window.panels._references.values()))
        text = next(iter(window.panels._content.values())).get("1.0", "end")
        assert '"run_id"' in text
        assert "CAP_READ_EVIDENCE" in text
        window.open(reference)
        assert len(window.panels.tabs.tabs()) == 1
        window.panels.close(reference)
        assert not window.panels.tabs.tabs()
        window.open(reference)
        wait()
        assert len(window.panels.tabs.tabs()) == 1
        assert window.transcript.get("1.0", "end") == before
        assert len(window.split.panes()) == 2
    finally:
        root.destroy()


def test_initial_slice_cannot_autoapprove():
    from llm_agent.application.approval import ApprovalPrompt, ApprovalResponse
    assert UnavailableApproval().present(ApprovalPrompt("write", "x", "?", {})) is ApprovalResponse.UNAVAILABLE
