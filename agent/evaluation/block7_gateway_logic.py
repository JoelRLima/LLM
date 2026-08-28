"""Responses for the deterministic Block 7 gateway."""

from __future__ import annotations

import json
from typing import Any

from agent.evaluation.block7_gateway_fixture import bind_code_task_objective
from agent.evaluation.block7_structured_proof_fixtures import H19_FINAL_ANSWERS, H19_PLAN_PAYLOADS
from agent.evaluation.block7_tool_guidance import h5_response, selection_response


def _scalar_plan(file_name: str) -> dict[str, Any]:
    return {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": file_name}}, {"tool": "grep", "args": {"path": "."}, "bindings": {"pattern": {"from_step": 1, "path": []}}}]}


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
    ("H13_SOURCE", {"action": "direct_response", "answer": "Resumo gerado a partir de foo.py; nenhuma escrita foi solicitada."}),
    (
        "H13_DEST",
        {
            "action": "use_tools",
            "plan": [{
                "tool": "code_task",
                "args": {
                    "action": "generate",
                    "objective": "H13_DEST",
                    "targets": ["resumo.md"],
                },
            }],
        },
    ),
    (
        "H13_MIXED",
        {
            "action": "use_tools",
            "plan": [{
                "tool": "code_task",
                "args": {
                    "action": "generate",
                    "objective": "H13_MIXED",
                    "targets": ["resumo.md"],
                },
            }],
        },
    ),
    (
        "H14_PT",
        {
            "action": "use_tools",
            "plan": [{
                "tool": "code_task",
                "args": {
                    "action": "modify",
                    "objective": "H14_PT",
                    "targets": ["permitido.txt"],
                },
            }],
        },
    ),
    (
        "H14_EN",
        {
            "action": "use_tools",
            "plan": [{
                "tool": "code_task",
                "args": {
                    "action": "modify",
                    "objective": "H14_EN",
                    "targets": ["allowed.txt"],
                },
            }],
        },
    ),
    (
        "H14_MIXED",
        {
            "action": "use_tools",
            "plan": [{
                "tool": "code_task",
                "args": {
                    "action": "modify",
                    "objective": "H14_MIXED",
                    "targets": ["permitido.txt"],
                },
            }],
        },
    ),
    ("H14_COPULA", {"action": "direct_response", "answer": "foo.py permaneceu intacto."}),
    ("H14_FORBIDDEN", {"action": "direct_response", "answer": "foo.py permaneceu intacto."}),
    (
        "H14_SCOPE",
        {"action": "use_tools", "plan": [{"tool": "code_task", "args": {"action": "modify", "objective": "H14_SCOPE", "targets": ["permitido.txt"]}}]},
    ),
    (
        "H15_UNRESOLVED",
        {
            "action": "use_tools",
            "plan": [
                {"tool": "file_reader", "args": {"file_path": "h15_condition.txt", "start_line": 1, "end_line": 1}},
                {
                    "kind": "deferred_condition",
                    "observation_ref": 1,
                    "predicate": {"op": "equals", "value": "H15_TRUE"},
                    "on_true": {
                        "tool": "code_task",
                        "args": {
                            "action": "generate",
                            "objective": "H15_TRUE",
                            "targets": ["h15_target.txt"],
                        },
                    },
                    "on_false": {"waive_effect": "write"},
                },
            ],
        },
    ),
    (
        "H15_FALSE",
        {
            "action": "use_tools",
            "plan": [
                {"tool": "file_reader", "args": {"file_path": "h15_condition.txt"}},
                {
                    "kind": "deferred_condition",
                    "observation_ref": 1,
                    "predicate": {"op": "equals", "value": "H15_TRUE"},
                    "on_true": {
                        "tool": "code_task",
                        "args": {
                            "action": "generate",
                            "objective": "H15_FALSE",
                            "targets": ["h15_target.txt"],
                        },
                    },
                    "on_false": {"waive_effect": "write"},
                },
            ],
        },
    ),
    (
        "H15_NEGATIVE",
        {
            "action": "use_tools",
            "plan": [
                {"tool": "file_reader", "args": {"file_path": "h15_condition.txt"}},
                {"kind": "deferred_condition", "observation_ref": 1,
                 "predicate": {"op": "equals", "value": "H15_FALSE"},
                 "on_true": {"tool": "code_task", "args": {"action": "generate", "objective": "H15_NEGATIVE", "targets": ["h15_target.txt"]}},
                 "on_false": {"waive_effect": "write"}},
            ],
        },
    ),
    (
        "H15_NEGPROHIB",
        {
            "action": "use_tools",
            "plan": [
                {"tool": "file_reader", "args": {"file_path": "h15_condition.txt"}},
                {
                    "kind": "deferred_condition",
                    "observation_ref": 1,
                    "predicate": {"op": "equals", "value": "H15_FALSE"},
                    "on_true": {"tool": "file_reader", "args": {"file_path": "h15_condition.txt"}},
                    "on_false": {"waive_effect": "write"},
                },
            ],
        },
    ),
    (
        "H15_TRUE",
        {
            "action": "use_tools",
            "plan": [
                {"tool": "file_reader", "args": {"file_path": "h15_condition.txt"}},
                {
                    "kind": "deferred_condition",
                    "observation_ref": 1,
                    "predicate": {"op": "equals", "value": "H15_TRUE"},
                    "on_true": {
                        "tool": "code_task",
                        "args": {
                            "action": "generate",
                            "objective": "H15_TRUE",
                            "targets": ["h15_target.txt"],
                        },
                    },
                    "on_false": {"waive_effect": "write"},
                },
            ],
        },
    ),
    (
        "H16_LICENSE1",
        {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": "pyproject.toml"}}]},
    ),
    (
        "H16_LICENSE2",
        {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": "pyproject.toml"}}]},
    ),
    ("H16_DEPENDENCIES", {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": "pyproject.toml"}}]}),
    ("H16_SUMMARY", {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": "pyproject.toml"}}]}),
    ("H16_ARBITRARY", {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": "config.toml"}}]}),
    ("H16_CONTENT", {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": "pyproject.toml"}}]}),
    ("H16_ENGLISH", {"action": "use_tools", "plan": [{"tool": "file_reader", "args": {"file_path": "package.json"}}]}),
    ("H16_CONCEPT", {"action": "direct_response", "answer": "Arquivo de configuração de projetos Python."}),
    (
        "H17_AUTONOMOUS",
        {
            "action": "use_tools",
            "plan": [{"tool": "code_task", "args": {"action": "modify", "objective": "H17_AUTO", "targets": ["notes.md"]}}],
        },
    ),
    (
        "H17_EXPLICIT",
        {
            "action": "use_tools",
            "plan": [{"tool": "code_task", "args": {"action": "modify", "objective": "H17_EXPLICIT", "targets": ["settings.json"]}}],
        },
    ),
    (
        "H17_EXTENSION",
        {
            "action": "use_tools",
            "plan": [{"tool": "code_task", "args": {"action": "modify", "objective": "H17_EXTENSION", "targets": ["extension.md"]}}],
        },
    ),
    ("H17_NEGATIVE", {"action": "direct_response", "answer": "protected.py permaneceu intacto por falta de autorizaÃ§Ã£o positiva."}),
    ("H17_AMBIGUOUS", {"action": "direct_response", "answer": "A alteraÃ§Ã£o de uncertain.py nÃ£o foi autorizada sem uma solicitaÃ§Ã£o inequÃ­voca."}),
    ("H18_NETWORK", {"action": "direct_response", "answer": "H18_SENTINEL foi tratado sem alterar arquivos."}),
)

_PLAN_PAYLOADS += H19_PLAN_PAYLOADS
def scripted_plan_response(objective: str, prompt: str) -> str:
    text = f"{objective}\n{prompt}"
    # Prefer the explicit fixture marker in the objective.  Several
    # conditional objectives mention another arm's literal (for example the
    # negative-predicate case mentions ``H15_FALSE``), so matching the full
    # prompt first would dispatch the wrong scripted branch.
    fixture_marker = objective.split(":", 1)[0].strip()
    runtime_objective = objective.split(":", 1)[1].strip() if ":" in objective else objective
    for marker, payload in _PLAN_PAYLOADS:
        if marker == fixture_marker:
            return json.dumps(bind_code_task_objective(payload, runtime_objective))
    for marker, payload in _PLAN_PAYLOADS:
        if marker in text:
            return json.dumps(bind_code_task_objective(payload, runtime_objective))
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


def _engineering_response(objective: str, prompt: str) -> str:
    combined = f"{objective}\n{prompt}"
    if "H19_POSITIVE" in combined:
        return json.dumps({"changes": [{"path": "h19_target.txt", "kind": "modify", "content": "H19_DONE\n"}]})
    if "H13_DEST" in combined or "H13_MIXED" in combined:
        return json.dumps({"changes": [{"path": "resumo.md", "kind": "create", "content": "Resumo de foo.py\n"}]})
    if "H14_PT" in combined or "H14_MIXED" in combined or "H14_SCOPE" in combined:
        return json.dumps({"changes": [{"path": "permitido.txt", "kind": "modify", "content": "alterado\n"}]})
    if "H14_EN" in combined:
        return json.dumps({"changes": [{"path": "allowed.txt", "kind": "modify", "content": "edited\n"}]})
    if "H15_TRUE" in combined or "H15_NEGATIVE" in combined:
        return json.dumps({"changes": [{"path": "h15_target.txt", "kind": "create", "content": "H15_DONE\n"}]})
    if "H17_AUTONOMOUS" in combined:
        return json.dumps({"changes": [{"path": "notes.md", "kind": "modify", "content": "H17_AUTO\n"}]})
    if "H17_EXPLICIT" in combined:
        return json.dumps({"changes": [{"path": "settings.json", "kind": "modify", "content": "H17_EXPLICIT\n"}]})
    if "H17_EXTENSION" in combined:
        return json.dumps({"changes": [{"path": "extension.md", "kind": "modify", "content": "H17_EXTENSION\n"}]})
    if "H12" not in combined:
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
    ("H10", "A condição H10_FALSE não autorizou a escrita de H10_EFFECT."),
    ("H12", "A alteração foi validada com sucesso."),
    ("H13_SOURCE", "O resumo foi gerado sem escrever no arquivo-fonte."),
    ("H13_DEST", "O resumo foi salvo em resumo.md."),
    ("H13_MIXED", "O resumo foi salvo em resumo.md; foo.py permaneceu como fonte."),
    ("H14_PT", "permitido.txt foi alterado; proibido.txt permaneceu intacto."),
    ("H14_EN", "allowed.txt was edited; forbidden.txt was left unchanged."),
    ("H14_MIXED", "permitido.txt foi alterado; proibido.txt não foi alterado."),
    ("H15_TRUE", "H15_DONE foi escrito no ramo verdadeiro."),
    ("H15_FALSE", "A condição foi falsa; a escrita não foi necessária."),
    ("H15_UNRESOLVED", "A observação permaneceu inconclusiva; nenhuma escrita foi executada."),
    ("H16_LICENSE1", "A licença observada em pyproject.toml é MIT."),
    ("H16_LICENSE2", "pyproject.toml informa a licença MIT."),
    ("H14_COPULA", "foo.py permaneceu intacto."),
    ("H14_FORBIDDEN", "foo.py permaneceu intacto."),
    ("H14_SCOPE", "permitido.txt foi alterado; foo.py permaneceu intacto."),
    ("H15_NEGATIVE", "H15_DONE foi escrito no ramo negativo."),
    ("H15_NEGPROHIB", "A condi\u00e7\u00e3o negativa foi confirmada; h15_target.txt permaneceu intacto."),
    ("H16_DEPENDENCIES", "pyproject.toml declara httpx como dependência."),
    ("H16_SUMMARY", "O projeto demo foi observado em pyproject.toml."),
    ("H16_ARBITRARY", "O campo banana_xyz em config.toml contem H16_VALUE."),
    ("H16_CONTENT", "O conteudo observado em pyproject.toml inclui H16_CONTENT_SENTINEL."),
    ("H16_ENGLISH", "package.json declara o script test."),
    ("H17_NEGATIVE", "protected.py permaneceu intacto por falta de autorizacao positiva."),
    ("H17_AMBIGUOUS", "A alteracao de uncertain.py nao foi autorizada sem uma solicitacao inequivoca."),
    ("H16_CONCEPT", "Arquivo de configuração de projetos Python."),
)


def _final_response(objective: str, prompt: str) -> str:
    text = f"{objective}\n{prompt}"
    if "H15_UNRESOLVED" in text:
        return next(answer for marker, answer in _FINAL_ANSWERS if marker == "H15_UNRESOLVED")
    if "H15_NEGATIVE" in text:
        return next(answer for marker, answer in _FINAL_ANSWERS if marker == "H15_NEGATIVE")
    if "H15_NEGPROHIB" in text:
        return next(answer for marker, answer in _FINAL_ANSWERS if marker == "H15_NEGPROHIB")
    if "H15_FALSE" in text:
        return next(answer for marker, answer in _FINAL_ANSWERS if marker == "H15_FALSE")
    for marker, answer in (*_FINAL_ANSWERS, *H19_FINAL_ANSWERS):
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
    """Dispatch the deterministic fixture without changing product policy.

    Discovery responses select from the exact current index.
    All other responses retain the pre-existing scenario handlers.
    Selection remains visibility-only and never authorizes a tool.
    This adapter is used only by the local deterministic campaign.
    """
    dispatch_objective = getattr(gateway, "dispatch_objective", gateway.objective)
    combined = f"{dispatch_objective}\n{prompt}"
    if "You are a Router Agent" in system:
        return '{"persona":"coder"}'
    if "TOOL DISCOVERY" in prompt:
        return selection_response(prompt)
    for handler in (_repair_response, _h11_response):
        response = handler(combined, prompt)
        if response is not None:
            return response
    response = h5_response(gateway, combined, prompt)
    return response if response is not None else _standard_response(dispatch_objective, prompt)


__all__ = ["scripted_plan_response", "scripted_response"]
