"""Responses for the deterministic Block 7 gateway."""

from __future__ import annotations

import json
from typing import Any


def _scalar_plan(file_name: str) -> dict[str, Any]:
    return {
        "action": "use_tools",
        "plan": [
            {"tool": "file_reader", "args": {"file_path": file_name}},
            {
                "tool": "grep",
                "args": {"path": "."},
                "bindings": {"pattern": {"from_step": 1, "path": []}},
            },
        ],
    }


_PLAN_PAYLOADS: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "B7_INVALID_DUPLICATE",
        {
            "action": "use_tools",
            "plan": [
                {"tool": "file_reader", "args": {"file_path": "fonte_h4.txt"}},
                {
                    "tool": "grep",
                    "args": {"pattern": "H4_VALUE", "path": "."},
                    "bindings": {"pattern": {"from_step": 1, "path": []}},
                },
            ],
        },
    ),
    ("H1_DIRECT", {"action": "direct_response", "answer": "abacaxi azul"}),
    (
        "H1_WORKSPACE",
        {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": "h1_observation.txt"}}]},
    ),
    ("H2", _scalar_plan("fonte_h2.txt")),
    (
        "H3",
        {
            "action": "use_tools",
            "plan": [
                {"tool": "grep", "args": {"pattern": "H3_SOURCE_MARKER", "path": "."}},
                {
                    "tool": "grep",
                    "args": {"path": "."},
                    "bindings": {"pattern": {"from_step": 1, "path": [0, "content"]}},
                },
            ],
        },
    ),
    ("H4", _scalar_plan("fonte_h4.txt")),
    (
        "H5",
        {
            "action": "continue_after_plan",
            "plan": [{"tool": "file_reader", "args": {"file_path": "h5_first.txt"}}],
        },
    ),
    (
        "H6",
        {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": 123}}]},
    ),
    (
        "H7",
        {"action": "use_tools", "plan": [{"tool": "grep", "args": {"pattern": "H7_EMPTY_SENTINEL", "path": "."}}]},
    ),
    (
        "H8",
        {"action": "use_tools", "plan": [{"tool": "grep", "args": {"pattern": "[", "path": "."}}]},
    ),
    (
        "H9",
        {
            "action": "use_tools",
            "plan": [
                {
                    "tool": "grep",
                    "args": {"pattern": "H9_TRUNCATED_SENTINEL", "path": ".", "max_results": 1},
                }
            ],
        },
    ),
    (
        "H10",
        {
            "action": "use_tools",
            "plan": [
                {"tool": "file_reader", "args": {"file_path": "h10_condition.txt"}},
                {
                    "kind": "deferred_condition",
                    "observation_ref": 1,
                    "predicate": {"op": "equals", "value": "H10_TRUE"},
                    "on_true": {
                        "tool": "code_task",
                        "args": {
                            "action": "modify",
                            "objective": "H10_EFFECT: altere h10_condition.txt para H10_EFFECT",
                            "targets": ["h10_condition.txt"],
                        },
                    },
                    "on_false": {"waive_effect": "write"},
                },
            ],
        },
    ),
    (
        "H11_MISSING",
        {
            "action": "use_tools",
            "plan": [{"tool": "file_reader", "args": {"file_path": "../h11_missing.txt"}}],
        },
    ),
    (
        "H11_PRESENT",
        {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": "h11_present.txt"}}]},
    ),
    (
        "H12",
        {
            "action": "use_tools",
            "plan": [
                {
                    "tool": "code_task",
                    "args": {
                        "action": "modify",
                        "objective": "H12: altere h12_module.py para retornar 2",
                        "targets": ["h12_module.py"],
                    },
                }
            ],
        },
    ),
)


def scripted_plan_response(objective: str, prompt: str) -> str:
    text = f"{objective}\n{prompt}"
    for marker, payload in _PLAN_PAYLOADS:
        if marker in text:
            return json.dumps(payload)
    return '{"action":"direct_response","answer":"sem decisão"}'


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


def _h5_response(gateway: Any, combined: str, prompt: str) -> str | None:
    if "Uma fronteira sem" not in prompt or "H5" not in combined:
        return None
    if len(gateway.calls) >= 4:
        return '{"action":"complete","reason":"H5_FINAL_EVIDENCE basta"}'
    return json.dumps(
        {"action": "execute", "plan": [{"tool": "file_reader", "args": {"file_path": "h5_second.txt"}}]}
    )


def _engineering_response(objective: str, prompt: str) -> str:
    if "H12" not in f"{objective}\n{prompt}":
        return json.dumps({"changes": []})
    return json.dumps(
        {
            "changes": [
                {
                    "path": "h12_module.py",
                    "kind": "edit",
                    "edits": [
                        {
                            "operation": "replace",
                            "start_line": 2,
                            "end_line": 2,
                            "content": "    return 2\n",
                        }
                    ],
                }
            ]
        }
    )


_FINAL_ANSWERS = (
    ("H1_WORKSPACE", "Foi observado H1_OBSERVED_EVIDENCE no arquivo real."),
    ("H2", "A busca usou o escalar observado orion_584271."),
    ("H3", "A observação aninhada confirmou H3_NESTED_VALUE."),
    ("H4", "A busca usou o valor H4_VALUE observado."),
    ("H5", "A primeira observação foi insuficiente; H5_FINAL_EVIDENCE foi confirmado na continuação."),
    ("H7", "H7_EMPTY_SENTINEL não foi observado; a coleção vazia não prova uma falha nem autoriza inventar arquivos."),
    ("H9", "A observação de H9_TRUNCATED_SENTINEL foi truncada e não é exaustiva."),
    ("H10", "A condição H10_FALSE não autorizou H10_EFFECT."),
    ("H12", "A alteração foi validada com sucesso."),
)


def _final_response(objective: str, prompt: str) -> str:
    text = f"{objective}\n{prompt}"
    for marker, answer in _FINAL_ANSWERS:
        if marker in text:
            return answer
    return "A resposta foi limitada às observações reais."


def _standard_response(objective: str, prompt: str) -> str:
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


def scripted_response(gateway: Any, system: str, prompt: str) -> str:
    combined = f"{gateway.objective}\n{prompt}"
    if "You are a Router Agent" in system:
        return '{"persona":"coder"}'
    for handler in (_repair_response, _h11_response):
        response = handler(combined, prompt)
        if response is not None:
            return response
    response = _h5_response(gateway, combined, prompt)
    return response if response is not None else _standard_response(gateway.objective, prompt)


__all__ = ["scripted_plan_response", "scripted_response"]
