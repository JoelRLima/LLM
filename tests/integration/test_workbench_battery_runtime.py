"""Desktop bridge through the real bounded worker and canonical task runtime."""
from __future__ import annotations

import json
import time
from dataclasses import replace

from llm_agent.agent.evaluation.weak_model_fixtures import WEAK_MODEL_FIXTURES
from llm_agent.agent.runtime.config_repository import ConfigRepository
from llm_agent.application import test_queue_runtime as adapter
from llm_agent.application.context import AppPaths
from llm_agent.application.task_execution import TaskExecutionStart
from llm_agent.execution.command import CommandExecutor
from llm_agent.interactive.session import InteractiveSession
from llm_agent.interfaces.workbench.webview import WorkbenchBridge


def test_desktop_battery_reaches_real_worker_and_grades_written_artifact(tmp_path, monkeypatch):
    scenario = next(item for item in WEAK_MODEL_FIXTURES if item.scenario_id == "WM-JSON")
    source = tmp_path / "tasks"
    task = source / "01-json"
    (task / "seed").mkdir(parents=True)
    (task / "task.md").write_text(scenario.objective)
    for name, content in scenario.initial_files.items():
        path = task / "seed" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (task / "checks.json").write_text(json.dumps({"checks": [
        {"kind": "json_equals", "path": "settings.json", "expected": {"enabled": True, "retries": 2}}]}))
    workspace = tmp_path / "project"
    workspace.mkdir()
    paths = AppPaths.discover(tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    bootstrap = (
        "import sys; sys.path.insert(0,sys.argv[1]); "
        "from llm_agent.agent.application import AgentApplication; "
        "from llm_agent.agent.evaluation.weak_model_fixtures import WEAK_MODEL_FIXTURES, WeakModelScriptedGateway; "
        "scenario=next(x for x in WEAK_MODEL_FIXTURES if x.scenario_id=='WM-JSON'); "
        "create=AgentApplication.create; "
        "AgentApplication.create=lambda **kw:create(**kw,gateway=WeakModelScriptedGateway(scenario.objective,fixture_marker='WM-JSON')); "
        "from llm_agent.application.test_queue_runtime import worker; worker()"
    )
    class ScriptedProviderProcess:
        def execute(self, request):
            return CommandExecutor().execute(replace(request, argv=("-I", "-c", bootstrap, request.argv[-1])))
    monkeypatch.setattr(adapter, "CommandExecutor", ScriptedProviderProcess)
    session = InteractiveSession(TaskExecutionStart(workspace, paths, configure_logging=False))
    bridge = WorkbenchBridge(session=session)
    try:
        selected = bridge.test_queue_select(session.workspace.workspace_id, "create", str(source))
        assert selected["accepted"]
        args = (session.workspace.workspace_id, selected["queue_id"], selected["queue"]["manifest_hash"])
        options = {"profile": "local_8gb", "live_authorized": True, "automatic_approval": True,
                   "case_seconds": 300, "total_seconds": 3600, "skip_interrupted": False}
        assert bridge.test_queue_action(*args, "check", "", options)["accepted"]
        assert bridge.test_queue_start(*args, options)["accepted"]
        events = []
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            events.extend(bridge.wait_events(50))
            if any(item["type"] in {"settled", "error"} for item in events):
                break
        terminal = next(item for item in events if item["type"] in {"settled", "error"})
        assert terminal["type"] == "settled", terminal
        assert "Verificado: 1" in terminal["observation"]["answer"]
        state = bridge.test_queue_state(session.workspace.workspace_id)
        assert state["queue"]["counts"] == {"verified": 1}
        detail = bridge.test_queue_action(*args, "details", "01-json")["details"]
        assert detail["execution"]["process"]["return_code"] == 0
        assert detail["verification"]["status"] == "passed"
        assert detail["execution"]["report_path"]
    finally:
        session.close().result(10)
