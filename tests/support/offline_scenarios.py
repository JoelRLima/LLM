from __future__ import annotations

import io
import json
import shutil
from pathlib import Path
from typing import Any, Dict, Iterator, Sequence

from rich.console import Console

from agent.evaluation import CapabilityScenario, ExecutionObservation
from agent.interfaces.cli.chat import run_chat_turn
from agent.llm.contracts import (
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    StreamEvent,
    StreamEventType,
)
from agent.llm.session import ChatSession
from agent.skills import load_skill_registry
from tests.support.task_definition import task_definition_response


class OfflineModelGateway:
    """In-memory model boundary used to exercise real code workflows."""

    provider_name = "offline-fixture"
    supports_task_definition = True
    capabilities = ProviderCapabilities(streaming=False)

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[ModelRequest] = []
        self._last_task_contract = None

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        authority = task_definition_response(self, request)
        if authority is not None:
            return ModelResponse(content=authority)
        if not self.responses:
            raise AssertionError("O cenário não forneceu resposta de modelo suficiente.")
        response = self.responses.pop(0)
        content = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        return ModelResponse(content=content)

    def stream(self, request: ModelRequest) -> Iterator[StreamEvent]:
        del request
        raise AssertionError("O baseline de código não deve solicitar streaming.")

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)


class OfflineChatGateway(OfflineModelGateway):
    """Canonical in-memory streaming chat gateway with no sockets or HTTP calls."""

    model = "offline-chat"
    profile = {"temperature": 0.0, "max_tokens": 128}
    capabilities = ProviderCapabilities(streaming=True)

    def __init__(self, response: str) -> None:
        super().__init__([response])

    def stream(self, request: ModelRequest) -> Iterator[StreamEvent]:
        self.calls.append(request)
        authority = task_definition_response(self, request)
        if authority is not None:
            content = authority
        elif self.responses:
            response = self.responses.pop(0)
            content = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        else:
            raise AssertionError("O cenário não forneceu resposta de chat suficiente.")
        yield StreamEvent(StreamEventType.CONTENT, text=content)
        yield StreamEvent(StreamEventType.DONE, data={"prompt_n": 1, "predicted_n": 1})


class OfflineScenarioExecutor:
    """Runs fixture-declared journeys through current production entrypoints."""

    def __init__(self, scenario: CapabilityScenario) -> None:
        self.scenario = scenario
        self.model_calls = 0

    def execute(self, objective: str, workspace: Path) -> ExecutionObservation:
        execution = self.scenario.metadata.get("execution")
        if not isinstance(execution, dict):
            raise ValueError(f"Cenário '{self.scenario.scenario_id}' não declara execução.")
        entrypoint = execution.get("entrypoint")
        if entrypoint == "chat":
            return self._run_chat(objective, execution)
        if entrypoint in {"code_task", "builtin_skill"}:
            return self._run_skill(objective, workspace, execution)
        raise ValueError(f"Entrypoint de baseline desconhecido: {entrypoint!r}.")

    @staticmethod
    def _run_chat(
        objective: str,
        execution: Dict[str, Any],
    ) -> ExecutionObservation:
        gateway = OfflineChatGateway(str(execution.get("response", "")))
        session = ChatSession(
            "Você é um assistente de teste.",
            {"model": "offline-chat", "hardware_profile": "low_vram_8gb"},
            gateway=gateway,
        )
        output = io.StringIO()
        run_chat_turn(Console(file=output, force_terminal=False), session, objective, 0)
        answer = session.messages[-1]["content"] if session.messages[-1]["role"] == "assistant" else ""
        return ExecutionObservation(
            success=bool(answer),
            answer=answer,
            steps=1,
            artifacts=[
                {
                    "kind": "chat_request",
                    "request": {
                        "model": gateway.calls[0].model,
                        "messages": [
                            {"role": message.role, "content": message.content}
                            for message in gateway.calls[0].messages
                        ],
                        "stream": gateway.calls[0].stream,
                    },
                }
            ],
        )

    def _run_skill(
        self,
        objective: str,
        workspace: Path,
        execution: Dict[str, Any],
    ) -> ExecutionObservation:
        gateway = OfflineModelGateway(list(execution.get("model_responses", [])))
        registry = load_skill_registry(
            base_dir=workspace,
            model_gateway=gateway,
            config={
                "auto_confirm": True,
                "hardware_profile": "low_vram_8gb",
                "max_io_concurrency": 1,
                "max_model_concurrency": 1,
            },
        )
        skill_name = str(execution.get("skill", "code_task"))
        args = dict(execution.get("args", {}))
        args.setdefault("objective", objective)
        result = registry.skill(skill_name).execute(args)
        self.model_calls = len(gateway.calls)
        observation = self._observation(result, self.model_calls)
        self._remove_validation_artifacts(workspace)
        return observation

    @staticmethod
    def _remove_validation_artifacts(workspace: Path) -> None:
        """Keep evaluator snapshots focused on user-visible filesystem effects."""

        for path in sorted(workspace.rglob("__pycache__"), reverse=True):
            if path.is_dir():
                shutil.rmtree(path)
        pytest_cache = workspace / ".pytest_cache"
        if pytest_cache.is_dir():
            shutil.rmtree(pytest_cache)

    @staticmethod
    def _observation(result: Dict[str, Any], model_calls: int) -> ExecutionObservation:
        data = result.get("data")
        data_dict = data if isinstance(data, dict) else {}
        raw_diagnostics = data_dict.get("diagnostics", [])
        raw_artifacts = data_dict.get("artifacts", [])
        diagnostics = list(raw_diagnostics) if isinstance(raw_diagnostics, Sequence) else []
        artifacts = list(raw_artifacts) if isinstance(raw_artifacts, Sequence) else []
        raw_order = data_dict.get("metadata", {}).get("execution_order", [])
        execution_order = list(raw_order) if isinstance(raw_order, Sequence) else []
        answer_parts = [str(result.get("message") or "")]
        for diagnostic in diagnostics:
            if isinstance(diagnostic, dict):
                answer_parts.append(
                    f"{diagnostic.get('file_path', '')}, linha "
                    f"{diagnostic.get('line', '')}: {diagnostic.get('message', '')}"
                )
        answer_parts.append(json.dumps(data, ensure_ascii=False, default=str))
        steps = len(execution_order)
        return ExecutionObservation(
            success=bool(result.get("ok")),
            answer="\n".join(answer_parts),
            steps=max(1, steps, model_calls),
            diagnostics=diagnostics,
            artifacts=artifacts,
            error=str(result.get("error")) if result.get("error") else None,
        )
