"""Real canonical runtime with a scripted model boundary; no provider calls."""
import io
import json
from dataclasses import replace

from llm_agent.agent.application import AgentApplication
from llm_agent.agent.evaluation.weak_model_fixtures import WEAK_MODEL_FIXTURES, WeakModelScriptedGateway
from llm_agent.agent.runtime.config_repository import packaged_config_defaults
from llm_agent.application import test_queue_runtime as adapter
from llm_agent.application.test_queue import TestQueue
from llm_agent.execution.command import CommandExecutor


def test_user_batch_reaches_real_task_runtime_and_checks_disk(tmp_path, monkeypatch):
    task = tmp_path / "tasks" / "01-modify"
    (task / "seed").mkdir(parents=True)
    scenario = next(item for item in WEAK_MODEL_FIXTURES if item.scenario_id == "WM-JSON")
    (task / "task.md").write_text(scenario.objective)
    for name, content in scenario.initial_files.items():
        path = task / "seed" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (task / "checks.json").write_text(json.dumps({"checks": [
        {"kind": "json_equals", "path": "settings.json", "expected": {"enabled": True, "retries": 2}}]}))
    queue = TestQueue(tmp_path / "queue")
    queue.create(tmp_path / "tasks")
    create = AgentApplication.create
    gateways = []
    def create_scripted(**kwargs):
        gateway = WeakModelScriptedGateway(scenario.objective, fixture_marker="WM-JSON")
        gateways.append(gateway)
        return create(**kwargs, gateway=gateway)
    monkeypatch.setattr(AgentApplication, "create", create_scripted)
    def execute(case, workspace, cancel, seconds):
        result_path = workspace.parent / "execution.json"
        payload = {"workspace": str(workspace), "result": str(result_path), "prompt": case.prompt,
                   "config": packaged_config_defaults(), "automatic_approval": True}
        monkeypatch.setattr(adapter.sys, "stdin", io.StringIO(json.dumps(payload)))
        adapter.worker()
        return json.loads(result_path.read_text())
    state = queue.run(execute, identity="scripted-boundary")
    row = state["results"]["01-modify"]
    assert row["status"] == "verified", row
    assert row["execution"]["success"] is True
    assert row["execution"]["report_path"]
    assert gateways[0].calls
    actual = queue.root / "cases" / "01-modify" / "workspace" / "settings.json"
    assert json.loads(actual.read_text()) == {"enabled": True, "retries": 2}


def test_bounded_worker_subprocess_uses_canonical_runtime_without_provider(tmp_path, monkeypatch):
    scenario = next(item for item in WEAK_MODEL_FIXTURES if item.scenario_id == "WM-JSON")
    task = tmp_path / "tasks" / "01-json"
    (task / "seed").mkdir(parents=True)
    (task / "task.md").write_text(scenario.objective)
    for name, content in scenario.initial_files.items():
        path = task / "seed" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (task / "checks.json").write_text(json.dumps({"checks": [
        {"kind": "json_equals", "path": "settings.json", "expected": {"enabled": True, "retries": 2}}]}))
    queue = TestQueue(tmp_path / "queue")
    queue.create(tmp_path / "tasks")
    bootstrap = (
        "import sys; sys.path.insert(0,sys.argv[1]); "
        "from llm_agent.agent.application import AgentApplication; "
        "from llm_agent.agent.evaluation.weak_model_fixtures import WEAK_MODEL_FIXTURES, WeakModelScriptedGateway; "
        "scenario=next(x for x in WEAK_MODEL_FIXTURES if x.scenario_id=='WM-JSON'); "
        "create=AgentApplication.create; "
        "AgentApplication.create=lambda **kw:create(**kw,gateway=WeakModelScriptedGateway(scenario.objective,fixture_marker='WM-JSON')); "
        "from llm_agent.application.test_queue_runtime import worker; worker()"
    )
    class ScriptedProcess:
        def execute(self, request):
            # Substitute only the provider boundary inside the real worker.
            return CommandExecutor().execute(replace(request, argv=("-I", "-c", bootstrap, request.argv[-1])))
    monkeypatch.setattr(adapter, "CommandExecutor", ScriptedProcess)
    state = queue.run(adapter.RuntimeExecutor(packaged_config_defaults(), automatic_approval=True), identity="scripted-worker")
    row = state["results"]["01-json"]
    assert row["status"] == "verified", row
    assert row["execution"]["process"]["return_code"] == 0
    assert (queue.root / "cases" / "01-json" / "stdout.log").is_file()
