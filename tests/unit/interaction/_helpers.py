from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from agent.llm.contracts import ModelResponse, ProviderCapabilities, StructuredOutputMode, TokenUsage
from agent.llm.session import ChatSession


class FakeGateway:
    provider_name = "wave12-test"

    def __init__(
        self,
        responses: list[str] | None = None,
        *,
        capabilities: ProviderCapabilities | None = None,
        measurements: list[Any] | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.capabilities = capabilities or ProviderCapabilities(
            streaming=False,
            structured_output_modes=(StructuredOutputMode.JSON_PROMPT,),
            reasoning=False,
        )
        self.measurements = list(measurements or [])
        self.calls: list[Any] = []
        self.measure_calls = 0

    def complete(self, request: Any) -> ModelResponse:
        self.calls.append(request)
        if not self.responses:
            raise AssertionError("fixture has no model response")
        return ModelResponse(
            content=self.responses.pop(0),
            usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )

    def stream(self, request: Any) -> Any:
        del request
        raise AssertionError("streaming is not used by this fixture")

    def measure_request_input_tokens(self, request: Any) -> Any:
        del request
        self.measure_calls += 1
        return self.measurements.pop(0) if self.measurements else None

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)


def decision(**overrides: Any) -> str:
    value: dict[str, Any] = {
        "action": "respond",
        "directive": "none",
        "ambiguity": "none",
        "grounding": "none",
        "operation_requested": False,
        "proposal_only": False,
        "resume_requested": False,
        "evidence": "",
    }
    value.update(overrides)
    return json.dumps(value, ensure_ascii=False)


def session(
    responses: list[str] | None = None,
    *,
    capabilities: ProviderCapabilities | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[ChatSession, FakeGateway]:
    gateway = FakeGateway(responses, capabilities=capabilities)
    values = {"hardware_profile": "low_vram_8gb", "ENABLE_GBNF": False}
    values.update(config or {})
    return ChatSession("test system", values, gateway=gateway), gateway


def application(responses: list[str] | None = None, **kwargs: Any) -> Any:
    current_session, gateway = session(responses, **kwargs)
    return SimpleNamespace(session=current_session, gateway=gateway)
