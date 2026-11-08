"""Trusted, bounded format feedback for an already-authorized decision retry."""
from __future__ import annotations

from typing import Any

from llm_agent.agent.llm.context_projection import ContextFitError
from llm_agent.agent.llm.contracts import ModelResponse, response_text
from llm_agent.agent.llm.decision_contract import ModelRequestContract, request_contract_for_request
from llm_agent.agent.llm.model_json import extract_json_value
from llm_agent.agent.llm.structured_output_strategy import StructuredOutputError

MARKER = "HARNESS DECISION FORMAT FEEDBACK"


def _reason(response: ModelResponse | str) -> tuple[str, str]:
    if getattr(response, "finish_reason", None) == "length":
        return "MODEL_OUTPUT_INCOMPLETE", "The previous response ended at the output limit. Return a concise, complete decision."
    try:
        extract_json_value(response_text(response))
    except StructuredOutputError:
        return "MODEL_JSON_INVALID", "The previous response was not one unambiguous complete JSON value."
    return "MODEL_DECISION_INVALID", "The previous JSON did not match the requested decision contract."


class DecisionRepairFeedback:
    """One call-local note; never copies model output into an instruction."""

    def __init__(self, contract: ModelRequestContract | None) -> None:
        self.contract = contract
        self.note = ""

    def observe(self, response: ModelResponse | str, decision: dict[str, Any] | None, request: Any) -> None:
        if decision is not None or self.contract is None or getattr(response, "finish_reason", None) == "content_filter":
            return
        if request_contract_for_request(request, request_contract=self.contract) is not self.contract:
            return
        code, explanation = _reason(response)
        self.note = (
            f"{MARKER}\nreason_code={code}\nrequired_contract={self.contract.value}\n{explanation}\n"
            "Return exactly one JSON object using the allowed fields and action variants in the original request. "
            "Do not add Markdown, duplicate keys, non-finite numbers or extra prose. "
            "Keep the original task, evidence requirements and tool permissions; do not invent missing facts."
        )

    def messages(self, original: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.note:
            return original
        if not original or original[-1].get("role") != "user" or not isinstance(original[-1].get("content"), str):
            raise ContextFitError("Decision repair requires the original user request.")
        messages = [message.copy() for message in original]
        messages[-1]["content"] += "\n\n" + self.note
        return messages
