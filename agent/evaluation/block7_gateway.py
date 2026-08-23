"""Deterministic Block 7 scripted gateway used before Phase 5."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from agent.evaluation.block7_gateway_logic import scripted_plan_response, scripted_response
from agent.evaluation.trace import RecordingGateway
from agent.llm.contracts import ModelRequest, ModelResponse, ProviderCapabilities, StreamEvent


class ScriptedBlock7Gateway:
    """Deterministic in-process gateway for the pre-Qwen contract exercise."""

    provider_name = "block7-scripted"
    model = "block7-scripted"
    profile = {"temperature": 0.0, "max_tokens": 512}
    endpoint_identity = "in-process://block7-scripted"
    provider_model_id = "block7-scripted"
    capabilities = ProviderCapabilities(streaming=False)

    def __init__(self, objective: str) -> None:
        self.objective = objective
        self.calls: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        system = str(request.messages[0].content) if request.messages else ""
        prompt = str(request.messages[-1].content) if request.messages else ""
        return ModelResponse(
            content=self._response(system, prompt),
            provider_metadata={"observed_provider_model_id": self.provider_model_id},
        )

    def stream(self, request: ModelRequest) -> Iterable[StreamEvent]:
        del request
        raise AssertionError("Block 7 scripted campaign must use canonical completion")

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    def _response(self, system: str, prompt: str) -> str:
        return scripted_response(self, system, prompt)

    def _plan_response(self, prompt: str) -> str:
        return scripted_plan_response(self.objective, prompt)


def _scripted_factory(objective: str, _workspace: Path) -> RecordingGateway:
    return RecordingGateway(ScriptedBlock7Gateway(objective))


__all__ = ["ScriptedBlock7Gateway"]
