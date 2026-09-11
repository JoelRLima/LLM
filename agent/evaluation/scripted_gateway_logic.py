"""Responses for the deterministic H-series gateway."""

from __future__ import annotations

import json
from typing import Any

from agent.evaluation.practical_gateway_logic import (
    practical_engineering_response,
    practical_final_answer,
    practical_plan_payload,
)
from agent.evaluation.scripted_gateway_fixture import bind_code_task_objective
from agent.evaluation.scripted_gateway_plans import SCRIPTED_PLAN_PAYLOADS as _PLAN_PAYLOADS
from agent.evaluation.structured_proof_fixtures import H19_FINAL_ANSWERS


def scripted_plan_response(objective: str, prompt: str) -> str:
    text = f"{objective}\n{prompt}"
    # Prefer the explicit fixture marker in the objective.  Several
    # conditional objectives mention another arm's literal (for example the
    # negative-predicate case mentions ``H15_FALSE``), so matching the full
    # prompt first would dispatch the wrong scripted branch.
    fixture_marker = objective.split(":", 1)[0].strip()
    runtime_objective = objective.split(":", 1)[1].strip() if ":" in objective else objective
    practical = practical_plan_payload(fixture_marker)
    if practical is not None:
        return json.dumps(bind_code_task_objective(practical, runtime_objective), ensure_ascii=False)
    for marker, payload in _PLAN_PAYLOADS:
        if marker == fixture_marker:
            return json.dumps(bind_code_task_objective(payload, runtime_objective))
    for marker, payload in _PLAN_PAYLOADS:
        if marker in text:
            return json.dumps(bind_code_task_objective(payload, runtime_objective))
    return '{"action":"direct_response","answer":"sem decisão"}'


def _plan_tool_names(value: Any, output: list[str], depth: int = 0) -> None:
    if depth > 16:
        return
    if isinstance(value, dict):
        tool = value.get("tool")
        if isinstance(tool, str) and tool not in output:
            output.append(tool)
        for child in value.values():
            _plan_tool_names(child, output, depth + 1)
    elif isinstance(value, list):
        for child in value[:64]:
            _plan_tool_names(child, output, depth + 1)


def scripted_required_tools(objective: str) -> tuple[str, ...]:
    """Read required tool names from the same fixture plan the gateway returns."""

    try:
        payload = json.loads(scripted_plan_response(objective, ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    names: list[str] = []
    _plan_tool_names(payload, names)
    return tuple(names)


_ENGINEERING_FIXTURES: tuple[tuple[tuple[str, ...], dict[str, Any]], ...] = (
    (("H19_POSITIVE",), {"path": "h19_target.txt", "kind": "modify", "content": "H19_DONE\n"}),
    (("H13_DEST", "H13_MIXED"), {"path": "resumo.md", "kind": "create", "content": "Resumo de foo.py\n"}),
    (("H14_PT", "H14_MIXED", "H14_SCOPE"), {"path": "permitido.txt", "kind": "modify", "content": "alterado\n"}),
    (("H14_EN",), {"path": "allowed.txt", "kind": "modify", "content": "edited\n"}),
    (("H15_TRUE", "H15_NEGATIVE"), {"path": "h15_target.txt", "kind": "create", "content": "H15_DONE\n"}),
    (("H17_AUTONOMOUS",), {"path": "notes.md", "kind": "modify", "content": "H17_AUTO\n"}),
    (("H17_EXPLICIT",), {"path": "settings.json", "kind": "modify", "content": "H17_EXPLICIT\n"}),
    (("H17_EXTENSION",), {"path": "extension.md", "kind": "modify", "content": "H17_EXTENSION\n"}),
)


def _known_engineering_response(combined: str) -> dict[str, Any] | None:
    for markers, change in _ENGINEERING_FIXTURES:
        if any(marker in combined for marker in markers):
            return change
    return None


def _h12_engineering_response() -> dict[str, Any]:
    return {
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


def _engineering_proposal(change: dict[str, Any] | None) -> str:
    return json.dumps(
        {
            "decision": "CHANGE" if change is not None else "NO_CHANGE",
            "rationale": (
                "A menor mudança necessária foi identificada na evidência do workspace."
                if change is not None
                else "Nenhuma mudança scripted foi solicitada."
            ),
            "reason_code": "NONE",
            "question": "",
            "changes": [change] if change is not None else [],
        },
        ensure_ascii=False,
    )


def _engineering_response(objective: str, prompt: str) -> str:
    combined = f"{objective}\n{prompt}"
    fixture_marker = objective.split(":", 1)[0].strip()
    practical = practical_engineering_response(fixture_marker, objective)
    if practical is not None:
        return practical
    known = _known_engineering_response(combined)
    if known is not None:
        return _engineering_proposal(known)
    if "H12" not in combined:
        return _engineering_proposal(None)
    return _engineering_proposal(_h12_engineering_response())


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
    fixture_marker = objective.split(":", 1)[0].strip()
    practical = practical_final_answer(fixture_marker)
    if practical is not None:
        return practical
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


def scripted_response(gateway: Any, system: str, prompt: str) -> str:
    from agent.evaluation.scripted_gateway_dispatch import dispatch_scripted_response

    return dispatch_scripted_response(gateway, system, prompt)


__all__ = ["scripted_plan_response", "scripted_response"]
