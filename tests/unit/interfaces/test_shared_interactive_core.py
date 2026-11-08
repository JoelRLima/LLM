"""Cross-adapter reuse without terminal, model or a second execution worker."""

from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from llm_agent.application.task_execution import TaskExecutionStart
from llm_agent.interactive import session as shared_session
from llm_agent.interactive.controller import InteractiveExecutionController
from llm_agent.interactive.session import InteractiveSession
from llm_agent.workspace.paths import AppPaths

ROOT = Path(__file__).resolve().parents[3]


def create_session(tmp_path):
    return InteractiveSession(TaskExecutionStart(tmp_path, AppPaths.discover(tmp_path / "home", env={})))


def test_legacy_facades_share_type_identity_and_terminal_projection_is_separate():
    from llm_agent.interactive.attention import ApprovalBroker
    from llm_agent.interactive.controller_models import SubmissionEnvelope
    from llm_agent.interactive.ui_plane import RunViewModel
    from llm_agent.interfaces.cli.attention import ApprovalBroker as LegacyBroker
    from llm_agent.interfaces.cli.controller import InteractiveExecutionController as LegacyController
    from llm_agent.interfaces.cli.controller_models import SubmissionEnvelope as LegacyEnvelope
    from llm_agent.interfaces.cli.ui_plane import RunViewModel as TerminalView

    assert LegacyController is InteractiveExecutionController
    assert LegacyEnvelope is SubmissionEnvelope
    assert LegacyBroker is ApprovalBroker
    assert issubclass(TerminalView, RunViewModel)
    assert not hasattr(RunViewModel(), "render_toolbar")
    assert TerminalView().render_toolbar() == "Ready | mode=FULL"


def test_shared_package_has_no_cli_or_ui_imports():
    for path in (ROOT / "src/llm_agent/interactive").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            imports = (
                [node.module]
                if isinstance(node, ast.ImportFrom)
                else [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for name in imports:
                assert not name.startswith(("llm_agent.interfaces", "prompt_toolkit", "rich", "webview", "tkinter")), (
                    path
                )
    code = """
import builtins,sys
sys.path.insert(0,'src')
original=builtins.__import__
def guarded(name,*args,**kwargs):
    if name.startswith('llm_agent.interfaces.cli'):
        raise AssertionError(name)
    return original(name,*args,**kwargs)
builtins.__import__=guarded
from llm_agent.interactive.session import InteractiveSession
assert not any(n.startswith('llm_agent.interfaces.cli') for n in sys.modules)
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_queries_and_session_inspection_work_without_config_or_model(tmp_path, monkeypatch):
    (tmp_path / "notes.txt").write_text("SHARED_LOCAL_EVIDENCE", encoding="utf-8")
    monkeypatch.setattr(
        shared_session, "start_interactive_session", lambda *a: pytest.fail("local query started Agent")
    )
    session = create_session(tmp_path)
    try:
        for text in ["/ls", "/read notes.txt", "/find SHARED_LOCAL_EVIDENCE"]:
            observation, ref = session.submit(text).result(2)
            assert observation["status"] == "succeeded"
            assert ref.run_id is None and ref.completeness == "unknown"
            assert session.read(ref).result(2).reference == ref
        for kind in ["files", "tasks"]:
            observation, ref = session.inspect(kind).result(2)
            assert observation["status"] == "succeeded"
            assert session.read(ref).result(2).sha256 is None
        for text in ["/unknown", "/code do something"]:
            with pytest.raises(ValueError):
                session.submit(text)
        assert session._runtime is None
    finally:
        session.close().result(2)


def test_reference_identity_and_workspace_confinement_are_preserved(tmp_path):
    (tmp_path / "safe.txt").write_text("safe", encoding="utf-8")
    session = create_session(tmp_path)
    try:
        _, ref = session.submit("/read safe.txt").result(2)
        with pytest.raises(ValueError):
            session.read(replace(ref, title="forged"))
        result, _ = session.submit("/read ../outside.txt").result(2)
        assert result["status"] == "failed"
        assert result["reason_code"] == "QUERY_PATH_INVALID"
        with pytest.raises(ValueError, match="INPUT_TOO_LARGE"):
            session.submit("x" * 16001)
    finally:
        session.close().result(2)


def prepare_fake_runtime(session, monkeypatch, execute):
    profile = SimpleNamespace(fingerprint="test-profile", api_url="http://localhost/v1/chat/completions", model="test", timeout=2)
    monkeypatch.setattr("llm_agent.interactive.availability.ConfigRepository.load", lambda *a, **kw: SimpleNamespace(model_profile=profile))
    session._artifacts = SimpleNamespace(offer_report=lambda _value: None)
    monkeypatch.setattr(session, "_ensure_runtime", lambda: object())
    monkeypatch.setattr(shared_session, "bind_submission_cancellation", lambda runtime, event, register: lambda: None)
    monkeypatch.setattr(shared_session, "execute_submission", execute)
    monkeypatch.setattr(shared_session, "observe_workbench_settlement", lambda value: value)


def test_shared_controller_cancels_twice_and_terminal_precedes_future_result(tmp_path, monkeypatch):
    session = create_session(tmp_path)
    started = Event()
    cancel_holder = {}

    def bind(runtime, event, register):
        cancel_holder["event"] = event
        return lambda: None

    def execute(runtime, text, **options):
        started.set()
        assert cancel_holder["event"].wait(2)
        return {"status": "failed", "reason_code": "INTERACTION_CANCELLED"}

    prepare_fake_runtime(session, monkeypatch, execute)
    monkeypatch.setattr(shared_session, "bind_submission_cancellation", bind)
    try:
        for generation in [1, 2]:
            started.clear()
            future = session.submit("cancel me")
            assert started.wait(2)
            assert session.controller.current_generation == generation
            session.cancel()
            observation, _ = future.result(2)
            assert observation == {"status": "failed", "reason_code": "INTERACTION_CANCELLED"}
            assert session.controller.is_busy() is False
            assert session.snapshot()["terminal_outcome"] == "failed"
    finally:
        session.close().result(2)


def test_provider_timeout_is_terminal_and_does_not_wedge_next_request(tmp_path, monkeypatch):
    session = create_session(tmp_path)

    def execute(*args, **kwargs):
        raise TimeoutError("provider deadline")

    prepare_fake_runtime(session, monkeypatch, execute)
    try:
        future = session.submit("first")
        with pytest.raises(TimeoutError, match="provider deadline"):
            future.result(2)
        assert future.done()
        assert not session.controller.is_busy()
        monkeypatch.setattr(shared_session, "execute_submission", lambda *a, **kw: {"status": "succeeded"})
        assert session.submit("second").result(2)[0]["status"] == "succeeded"
    finally:
        session.close().result(2)


def test_wait_timeout_does_not_lose_result_or_cancel_operation(tmp_path, monkeypatch):
    session = create_session(tmp_path)
    release = Event()
    started = Event()

    def execute(*args, **kwargs):
        started.set()
        assert release.wait(2)
        return {"status": "succeeded", "answer": "done"}

    prepare_fake_runtime(session, monkeypatch, execute)
    try:
        future = session.submit("wait")
        assert started.wait(2)
        with pytest.raises(TimeoutError):
            future.result(0)
        assert not future.done()
        release.set()
        assert future.result(2)[0]["status"] == "succeeded"
    finally:
        release.set()
        session.close().result(2)


def test_stream_is_shared_bounded_and_truncation_is_observable(tmp_path, monkeypatch):
    session = create_session(tmp_path)
    chunks = []
    produced = Event()

    def execute(runtime, text, **options):
        for _ in range(1000):
            options["stream_callback"]("x" * 1000)
        produced.set()
        return {"status": "succeeded", "answer": "original final response"}

    prepare_fake_runtime(session, monkeypatch, execute)
    try:
        future = session.submit("stream", stream_callback=chunks.append)
        assert produced.wait(2)
        result, _ = future.result(2)
        assert result["answer"] == "original final response"
        assert len("".join(chunks)) <= 64 * 1024
        assert session.last_stream_truncated
        assert session.controller.is_busy() is False
    finally:
        session.close().result(2)


def test_architecture_guard_inspects_shared_worker_and_not_only_facade(tmp_path, monkeypatch):
    from scripts import check_wave17_architecture as checker
    from scripts.w21_architecture.source import SourceLayout

    monkeypatch.setattr(checker, "w21_source_layout", lambda root: SourceLayout.for_profile(root, "final-w22"))
    for name in ["app", "interactive_session", "interactive_resources", "controller"]:
        target = tmp_path / "src/llm_agent/interfaces/cli" / (name + ".py")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / "src/llm_agent/interfaces/cli" / (name + ".py")).read_bytes())
    target = tmp_path / "src/llm_agent/interactive/controller.py"
    target.parent.mkdir(parents=True)
    original = (ROOT / "src/llm_agent/interactive/controller.py").read_text(encoding="utf-8")
    target.write_text(original, encoding="utf-8")
    assert checker._check_single_execution_owner(tmp_path) == []
    target.write_text(original.replace("daemon=False", "daemon=True"), encoding="utf-8")
    assert any("non-daemon" in f.detail for f in checker._check_single_execution_owner(tmp_path))


def test_bridge_offers_local_inspections_without_starting_model(tmp_path, monkeypatch):
    import importlib

    monkeypatch.syspath_prepend(str(ROOT / "prototypes/workbench-comparison"))
    bridge_module = importlib.import_module("pywebview_candidate")
    (tmp_path / "bridge.txt").write_text("LOCAL_BRIDGE", encoding="utf-8")
    session = create_session(tmp_path)
    monkeypatch.setattr(shared_session, "start_interactive_session", lambda *args: pytest.fail("model startup"))
    monkeypatch.setattr(bridge_module, "create_session", lambda **options: (session, tmp_path, None))
    bridge = bridge_module.WorkbenchBridge()
    try:
        for kind in ("files", "tasks"):
            accepted = bridge.inspect(kind)
            assert accepted["accepted"]
            with bridge._condition:
                assert bridge._condition.wait_for(lambda: bridge._active_request_id is None, 2)
            events = bridge.wait_events(0)
            settled = next(event for event in events if event["type"] == "settled")
            assert settled["request_id"] == accepted["request_id"]
            assert settled["reference"]["run_id"] is None
            content = bridge.read_report(settled["reference"]["token"])
            assert content["reference"]["source"] in (
                "ReadOnlyWorkspaceQueryService",
                "InteractiveExecutionController / RunViewModel",
            )
            assert content["text"]
        assert not bridge.inspect("invalid")["accepted"]
    finally:
        session.close().result(2)


def test_shared_session_keeps_canonical_report_and_inventory_without_tk_adapter(tmp_path, monkeypatch):
    import json

    from llm_agent.agent.application import AgentApplication
    from llm_agent.agent.llm.contracts import ProviderCapabilities
    from llm_agent.agent.runtime.config_repository import ConfigRepository
    from llm_agent.agent.tools.authority import OperationalMode
    from llm_agent.application.approval import _bind_approval_presenter
    from llm_agent.application.task_execution import _retain_runtime
    from tests.integration.test_agent_application_evaluation import JourneyGateway

    (tmp_path / "notes.txt").write_text("CAP_READ_EVIDENCE\n", encoding="utf-8")
    session = create_session(tmp_path)
    ConfigRepository(session.start.app_paths).initialize()
    objective = "CAP_READ: leia notes.txt e informe CAP_READ_EVIDENCE."
    gateway = JourneyGateway(objective)
    gateway.capabilities = ProviderCapabilities(streaming=True)

    def start(request, approval):
        return _retain_runtime(
            AgentApplication.create(
                paths=request.app_paths,
                workspace=request.workspace,
                gateway=gateway,
                approval_policy=_bind_approval_presenter(approval),
                operational_mode=OperationalMode.READ_ONLY,
                configure_logging=False,
            )
        )

    monkeypatch.setattr(shared_session, "start_interactive_session", start)
    chunks = []
    try:
        observation, ref = session.submit(objective, stream_callback=chunks.append).result(30)
        assert observation["status"] == "succeeded"
        original_generation = session.view.snapshot().generation
        content = session.read(ref).result(5)
        report = json.loads(content.text)
        assert report["run_id"] == ref.run_id == observation["run_id"]
        assert ref.source == "TaskReportBuilder"
        assert Path(observation["report_path"]).read_text(encoding="utf-8") == content.text
        assert chunks
        _, inventory = session.inspect("tools").result(5)
        assert "file_reader" in session.read(inventory).result(2).text
        assert session.view.snapshot().generation == original_generation
    finally:
        session.close().result(10)
