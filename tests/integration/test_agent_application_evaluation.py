from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterator

from agent.application import AgentApplication
from agent.approval import AutoApprove
from agent.evaluation import (
    AgentApplicationScenarioExecutor,
    CapabilityEvaluator,
    CapabilityScenario,
    ScenarioExpectation,
)
from agent.evaluation.curated import CURATED_CAPABILITY_SET
from agent.llm.contracts import ModelRequest, ModelResponse, ProviderCapabilities, StreamEvent
from agent.runtime.config_repository import ConfigRepository
from agent.runtime.paths import AppPaths
from agent.runtime.workspace_context import WorkspaceContext
from agent.tools.authority import TaskAuthoritySnapshot
from agent.tools.extension_catalog_service import ExtensionCatalogService
from agent.tools.extension_catalog_storage import ExtensionCatalogStorage
from agent.tools.workspace_extensions_service import WorkspaceExtensionService


class JourneyGateway:
    provider_name = "eval-scripted"
    model = "eval-scripted"
    profile = {"temperature": 0.0, "max_tokens": 128}
    capabilities = ProviderCapabilities(streaming=False)

    def __init__(self, objective: str) -> None:
        self.objective = objective
        self.calls: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        return ModelResponse(content=self._response(request.messages[0].content, request.messages[-1].content))

    def stream(self, request: ModelRequest) -> Iterator[StreamEvent]:
        del request
        raise AssertionError("eval journey must use non-streaming planner calls")

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    def build_payload(self, request: ModelRequest) -> Dict[str, Any]:
        return {"messages": [{"role": item.role, "content": item.content} for item in request.messages]}

    def complete_payload(self, payload: Dict[str, Any]) -> str:
        messages = payload.get("messages", [])
        self.calls.append(payload)  # type: ignore[arg-type]
        return self._response(str(messages[0].get("content", "")), str(messages[-1].get("content", "")))

    def send_payload(self, payload: Dict[str, Any], stream: bool) -> str:
        del stream
        return self.complete_payload(payload)

    def consume_stream(self, response: Any, callbacks: Dict[str, Any]) -> str:
        del callbacks
        return str(response)

    def _response(self, system: str, prompt: str) -> str:
        if "You are a Router Agent" in system:
            return '{"persona":"coder"}'
        if "Crie um plano sequencial" in prompt:
            return self._plan_response()
        if "Objetivo de engenharia:" in prompt and "CAP_MODIFY" in self.objective:
            return '{"changes":[{"path":"sample.py","kind":"edit","edits":[{"operation":"replace","start_line":1,"end_line":1,"content":"value = 2"}]}]}'
        if "Objetivo de engenharia:" in prompt and "CAP_RECOVERY" in self.objective:
            return '{"changes":[{"path":"sample.py","kind":"modify","content":"def value(:"}]}'
        if "Resultados das ferramentas executadas:" in prompt:
            return self._result_response(prompt)
        return '{"persona":"coder"}'

    def _plan_response(self) -> str:
        plans = (
            ("CAP_DENIAL", '{"plan":[{"tool":"file_reader","args":{"file_path":"../outside.txt"}}]}'),
            ("CAP_SEARCH", '{"plan":[{"tool":"grep","args":{"pattern":"CAP_SEARCH_EVIDENCE","path":"."}}]}'),
            ("CAP_MODIFY", '{"plan":[{"tool":"code_task","args":{"action":"modify","objective":"CAP_MODIFY","targets":["sample.py"]}}]}'),
            ("CAP_RECOVERY", '{"plan":[{"tool":"code_task","args":{"action":"modify","objective":"CAP_RECOVERY","targets":["sample.py"]}}]}'),
            ("CAP_EXTENSION", '{"plan":[{"tool":"demo_tool","args":{"text":"CAP_EXTENSION_EVIDENCE"}}]}'),
        )
        for marker, response in plans:
            if marker in self.objective:
                return response
        if "CAP_SHELL" in self.objective or "CAP_FAILURE" in self.objective:
            return '{"plan":[{"tool":"shell","args":{"command":"git log -1"}}]}'
        return '{"plan":[{"tool":"file_reader","args":{"file_path":"notes.txt"}}]}'

    def _result_response(self, prompt: str) -> str:
        responses = (
            ("CAP_READ_EVIDENCE", "A leitura encontrou CAP_READ_EVIDENCE no arquivo real."),
            ("CAP_SEARCH_EVIDENCE", "A busca encontrou CAP_SEARCH_EVIDENCE no arquivo real."),
            ("CAP_SHELL_EVIDENCE", "O histórico real confirma CAP_SHELL_EVIDENCE."),
            ("CAP_EXTENSION_EVIDENCE", "A extensão externa confirmou CAP_EXTENSION_EVIDENCE."),
        )
        if "acesso negado" in prompt.casefold():
            return "A leitura foi negada sem efeito fora do workspace."
        for marker, response in responses:
            if marker in prompt:
                return response
        if "CAP_MODIFY" in self.objective:
            return "A modificação foi validada com sucesso."
        if "CAP_RECOVERY" in self.objective:
            return "A validação falhou e o arquivo foi restaurado pelo rollback."
        if "CAP_FAILURE" in self.objective:
            return "A inspeção falhou; não foi possível concluir com sucesso."
        return "A tarefa foi concluída com a evidência retornada."


class ProviderFailureGateway:
    _secret_message = (
        "request failed api_key=TOPSECRET Authorization: Bearer TOPSECRET "
        "token=TOPSECRET password=TOPSECRET"
    )

    def build_payload(self, request: ModelRequest) -> Dict[str, Any]:
        return {"messages": [{"role": item.role, "content": item.content} for item in request.messages]}

    def complete_payload(self, payload: Dict[str, Any]) -> str:
        del payload
        raise RuntimeError(self._secret_message)

    def send_payload(self, payload: Dict[str, Any], stream: bool) -> str:
        del payload, stream
        raise RuntimeError(self._secret_message)

    def consume_stream(self, response: Any, callbacks: Dict[str, Any]) -> str:
        del response, callbacks
        raise RuntimeError(self._secret_message)

    def count_tokens(self, text: str) -> int:
        del text
        return 1


class RecoveringGateway(JourneyGateway):
    def __init__(self, objective: str) -> None:
        super().__init__(objective)
        self.fail_once = True

    def complete_payload(self, payload: Dict[str, Any]) -> str:
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("transient provider failure")
        return super().complete_payload(payload)


def _initialized_paths(tmp_path: Path) -> AppPaths:
    paths = AppPaths.discover(tmp_path / "home", env={})
    ConfigRepository(paths).initialize()
    return paths


def test_provider_failure_preserves_public_cause_and_report_outcome(tmp_path: Path, caplog: Any) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = _initialized_paths(tmp_path)

    with AgentApplication.create(
        paths=paths,
        workspace=workspace,
        gateway=ProviderFailureGateway(),
        configure_logging=False,
    ) as application:
        result = application.run("leia notes.txt")

    assert result.status == "failed"
    assert result.error == "Model provider request failed."
    for secret in ("TOPSECRET", "Authorization: Bearer", "api_key=", "token=", "password="):
        assert secret not in repr(result.to_dict())
    assert result.receipt["error"]["code"] == "MODEL_PROVIDER_ERROR"
    assert result.receipt["error"]["layer"] == "provider"
    assert result.report_path is not None
    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
    assert report["success"] is False
    assert report["status"] == "failed"
    run_metrics = [
        entry for entry in application.orchestrator._get_metrics_for_task()
        if entry.get("metric_type") == "run"
    ]
    assert len(run_metrics) == 1
    assert run_metrics[0]["success"] is False
    report_text = Path(result.report_path).read_text(encoding="utf-8")
    for secret in ("TOPSECRET", "Authorization: Bearer", "api_key=", "token=", "password="):
        assert secret not in report_text
        assert secret not in caplog.text


def test_reused_application_emits_one_run_metric_per_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("CAP_READ_EVIDENCE\n", encoding="utf-8")
    objective = "CAP_READ: leia notes.txt e informe CAP_READ_EVIDENCE."
    with AgentApplication.create(
        paths=_initialized_paths(tmp_path),
        workspace=workspace,
        gateway=JourneyGateway(objective),
        configure_logging=False,
    ) as application:
        first = application.run(objective)
        first_report = json.loads(Path(first.report_path).read_text(encoding="utf-8"))
        first_metrics = application.orchestrator._get_metrics_for_task()
        second = application.run(objective)
        second_report = json.loads(Path(second.report_path).read_text(encoding="utf-8"))
        second_metrics = application.orchestrator._get_metrics_for_task()

    first_run = [item for item in first_metrics if item.get("metric_type") == "run"]
    second_run = [item for item in second_metrics if item.get("metric_type") == "run"]
    assert first.success is True and second.success is True
    assert first_report["success"] is True and second_report["success"] is True
    assert len(first_run) == 1 and len(second_run) == 1
    assert first_run[0]["success"] is True and second_run[0]["success"] is True
    assert first_report["run_id"] != second_report["run_id"]
    assert first_run[0]["run_id"] == first_report["run_id"]
    assert second_run[0]["run_id"] == second_report["run_id"]


def test_reused_application_failed_then_successful_run_has_isolated_outcomes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("CAP_READ_EVIDENCE\n", encoding="utf-8")
    objective = "CAP_READ: leia notes.txt e informe CAP_READ_EVIDENCE."
    with AgentApplication.create(
        paths=_initialized_paths(tmp_path),
        workspace=workspace,
        gateway=RecoveringGateway(objective),
        configure_logging=False,
    ) as application:
        failed = application.run(objective)
        failed_report = json.loads(Path(failed.report_path).read_text(encoding="utf-8"))
        failed_metrics = application.orchestrator._get_metrics_for_task()
        succeeded = application.run(objective)
        succeeded_report = json.loads(Path(succeeded.report_path).read_text(encoding="utf-8"))
        succeeded_metrics = application.orchestrator._get_metrics_for_task()

    assert failed.success is False and failed_report["success"] is False
    assert succeeded.success is True and succeeded_report["success"] is True
    assert [item["success"] for item in failed_metrics if item.get("metric_type") == "run"] == [False]
    assert [item["success"] for item in succeeded_metrics if item.get("metric_type") == "run"] == [True]
    assert failed_report["run_id"] != succeeded_report["run_id"]


def test_application_receipt_projects_modify_validation_and_rollback(tmp_path: Path) -> None:
    for objective, expected_status, expected_validation, expected_rollback, expected_content in (
        ("CAP_MODIFY: altere sample.py", "succeeded", "passed", False, "value = 2"),
        ("CAP_RECOVERY: altere sample.py", "failed", "failed", True, "value = 1\n"),
    ):
        case_root = tmp_path / objective.split(":", 1)[0].lower()
        workspace = case_root / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "sample.py").write_text("value = 1\n", encoding="utf-8")
        with AgentApplication.create(
            paths=_initialized_paths(case_root),
            workspace=workspace,
            gateway=JourneyGateway(objective),
            approval_policy=AutoApprove(),
            configure_logging=False,
        ) as application:
            result = application.run(objective)
        assert result.status == expected_status
        assert result.receipt["files_affected"] == ["sample.py"]
        assert result.receipt["validation"] == {"ran": True, "outcome": expected_validation}
        assert result.receipt["rollback"]["occurred"] is expected_rollback
        assert result.receipt["final_state"] == ("restored" if expected_rollback else "applied")
        report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
        assert report["success"] is (expected_status == "succeeded")
        assert report["metrics"]["total_duration_ms"] > 0
        assert (workspace / "sample.py").read_text(encoding="utf-8") == expected_content


def test_capability_evaluator_uses_real_agent_application_path(tmp_path: Path) -> None:
    scenario = CapabilityScenario(
        "real-read",
        "read/search",
        "CAP_READ: leia notes.txt e informe CAP_READ_EVIDENCE.",
        initial_files={"notes.txt": "CAP_READ_EVIDENCE\n"},
        expectation=ScenarioExpectation(
            unchanged_files=("notes.txt",),
            answer_contains=("CAP_READ_EVIDENCE",),
            max_steps=2,
        ),
    )
    executor = AgentApplicationScenarioExecutor(lambda objective, workspace: JourneyGateway(objective))

    report = CapabilityEvaluator(executor).evaluate(scenario, tmp_path / "workspace")

    assert report.passed is True
    assert report.observation.measurement["tools"] == ["file_reader"]
    assert report.observation.measurement["model_calls"] >= 2
    assert report.observation.measurement["invocation_ids"]


def _prepare_git(objective: str, workspace: Path, _paths: Any) -> None:
    if "CAP_SHELL" not in objective:
        return
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "eval@example.invalid"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "Eval"], cwd=workspace, check=True)
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "CAP_SHELL_EVIDENCE"], cwd=workspace, check=True)


def test_curated_capability_set_uses_one_real_runner_and_covers_required_categories(tmp_path: Path) -> None:
    selected = {item.scenario_id: item for item in CURATED_CAPABILITY_SET}
    scenarios = (
        selected["cap-read"],
        selected["cap-search"],
        selected["cap-modify-validate"],
        selected["cap-shell"],
        selected["cap-no-tool"],
        selected["cap-failure"],
        selected["cap-denial-recovery"],
        selected["cap-recovery"],
    )

    report = CapabilityEvaluator(
        AgentApplicationScenarioExecutor(
            lambda objective, workspace: JourneyGateway(objective),
            approval_policy=AutoApprove(),
            prepare=_prepare_git,
        )
    ).evaluate_set(scenarios, tmp_path / "set")

    assert report.total == 8
    assert report.passed == 8, report.to_dict()
    assert {item.capability for item in scenarios} >= {
        "read/search", "modify/validate", "shell", "no-tool", "failure", "denial/recovery", "recovery/rollback"
    }


def _prepare_extension(objective: str, workspace: Path, paths: Any) -> None:
    if "CAP_EXTENSION" not in objective:
        return
    extension_dir = workspace.parent / "eval-extension"
    extension_dir.mkdir(parents=True, exist_ok=True)
    marker = workspace.parent / "extension-spawned.txt"
    (extension_dir / "tool.py").write_text(
        "import json\n"
        "import sys\n"
        "from pathlib import Path\n"
        "payload = json.loads(sys.stdin.read())\n"
        f"Path({str(marker)!r}).write_text('spawned', encoding='utf-8')\n"
        "text = payload.get('args', {}).get('text', '')\n"
        "print(json.dumps({'invocation_id': payload.get('invocation_id'), 'status': 'succeeded', 'message': text, 'data': {'echo': text}}), flush=True)\n",
        encoding="utf-8",
    )
    manifest = extension_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "id": "eval.extension",
                "version": "1.0.0",
                "protocol_version": "1.0",
                "transport": "stdio",
                "entrypoint": ["${python}", "${extension_dir}/tool.py"],
                "timeout_seconds": 5,
                "tools": [{
                    "name": "demo_tool",
                    "description": "deterministic eval extension",
                    "schema": {"type": "object", "properties": {"text": {"type": "string"}}},
                    "capabilities": ["read", "process"],
                }],
            }
        ),
        encoding="utf-8",
    )
    catalog = ExtensionCatalogService(ExtensionCatalogStorage(paths.extensions_catalog_file))
    catalog.add(manifest)
    workspace_id = WorkspaceContext.create(workspace).workspace_id
    service = WorkspaceExtensionService.for_workspace(paths, workspace_id, catalog)
    service.enable("eval.extension")
    service.grant("eval.extension", "read")
    service.grant("eval.extension", "process")


def test_curated_extension_uses_real_stdio_process_and_gateway(tmp_path: Path) -> None:
    scenario = next(item for item in CURATED_CAPABILITY_SET if item.scenario_id == "cap-extension")
    executor = AgentApplicationScenarioExecutor(
        lambda objective, workspace: JourneyGateway(objective),
        approval_policy=AutoApprove(),
        task_authority=TaskAuthoritySnapshot(frozenset({"read", "process"})),
        prepare=_prepare_extension,
    )

    report = CapabilityEvaluator(executor).evaluate(scenario, tmp_path / "extension-workspace")

    assert report.passed is True
    assert report.observation.measurement["tools"] == ["demo_tool"]
    assert (tmp_path / "extension-spawned.txt").read_text(encoding="utf-8") == "spawned"
