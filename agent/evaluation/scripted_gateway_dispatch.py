"""Dispatch helpers for the deterministic H-series gateway."""

from __future__ import annotations

import json
from typing import Any

from agent.evaluation.scripted_tool_guidance import h5_response, selection_response


def _repair_response(combined: str, prompt: str) -> str | None:
    if "CONSTRAINED VALIDATION REPAIR" not in prompt or "H6" not in combined:
        return None
    return json.dumps(
        {"action": "tool", "tool": "file_reader", "args": {"file_path": 123}}
    )

def _h11_response(combined: str, prompt: str) -> str | None:
    if "UNTRUSTED TOOL FAILURE EVIDENCE" in prompt and "H11" in combined:
        return '{"action":"final","answer":"falha parcial observada"}'
    if "Objetivo complexo:" not in prompt or "H11" not in combined:
        return None
    return json.dumps(
        {
            "steps": [
                {
                    "id": "h11-missing",
                    "title": "missing observation",
                    "goal": "H11_MISSING: leia h11_missing.txt",
                    "priority": "high",
                    "depends_on": [],
                    "estimated_tools": ["file_reader"],
                },
                {
                    "id": "h11-present",
                    "title": "present observation",
                    "goal": "H11_PRESENT: leia h11_present.txt",
                    "priority": "medium",
                    "depends_on": [],
                    "estimated_tools": ["file_reader"],
                },
            ]
        }
    )


def _standard_response(objective: str, prompt: str) -> str:
    from agent.evaluation.scripted_gateway_logic import (
        _engineering_response,
        _final_response,
        scripted_plan_response,
    )
    if "Escolha exatamente uma das duas respostas JSON" in prompt:
        return scripted_plan_response(objective, prompt)
    if "Uma fronteira sem" in prompt:
        # The production runtime now treats plan exhaustion as an
        # observation frontier, so every deterministic successful arm needs
        # the same explicit completion decision that a real model would make.
        # H5 has its own continuation sequence above; all other arms have
        # enough evidence at this boundary to close the task.
        return '{"action":"complete","reason":"as observacoes reais bastam"}'
    if "Objetivo de engenharia:" in prompt:
        return _engineering_response(objective, prompt)
    if "Resultados das ferramentas executadas:" in prompt:
        return _final_response(objective, prompt)
    if "Os resultados a seguir foram obtidos" in prompt:
        return "H11 terminou com falha parcial pública; o resultado posterior não apagou a falha."
    return '{"action":"final","answer":"decisão scripted"}'

def dispatch_scripted_response(gateway: Any, system: str, prompt: str) -> str:
    """Dispatch the deterministic fixture without changing product policy.

    Discovery responses select from the exact current index.
    All other responses retain the pre-existing scenario handlers.
    Selection remains visibility-only and never authorizes a tool.
    This adapter is used only by the local deterministic campaign.
    """
    from agent.evaluation.scripted_gateway_logic import scripted_required_tools

    dispatch_objective = getattr(gateway, "dispatch_objective", gateway.objective)
    combined = f"{dispatch_objective}\n{prompt}"
    if "You are a Router Agent" in system:
        return '{"persona":"coder"}'
    if "TOOL DISCOVERY" in prompt:
        return selection_response(
            prompt,
            required_tools=scripted_required_tools(dispatch_objective),
        )
    for handler in (_repair_response, _h11_response):
        response = handler(combined, prompt)
        if response is not None:
            return response
    response = h5_response(gateway, combined, prompt)
    return response if response is not None else _standard_response(dispatch_objective, prompt)


__all__ = ["dispatch_scripted_response"]
