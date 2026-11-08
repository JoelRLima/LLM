"""Prompt construction and caller-identity binding for TaskDefinitionCompiler."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from llm_agent.agent.llm.admitted_decisions import TaskContractDecision
from llm_agent.agent.task_definition.models import TaskContract


def _contract_prompt(task_id: str, objective: str) -> str:
    return (
        "Compile uma TaskContract normativa para a tarefa abaixo. Responda exclusivamente "
        "com o contrato fechado TASK_CONTRACT, sem markdown, prose, ferramentas, grants ou plano executavel.\n"
        f"task_id exato: {task_id}\nobjective exato: {objective}\n"
        'Forma permitida: {"action":"define_contract","contract":{"task_id":"...",'
        '"objective":"...","summary":"...","requirements":[],"constraints":[],'
        '"invariants":[],"success_criteria":[],"out_of_scope":[],"stop_conditions":[],'
        '"assumptions":[],"open_questions":[]}}. Tambem sao permitidos '
        '{\"action\":\"needs_input\",\"reason\":\"...\",\"question\":\"...\"} e '
        '{\"action\":\"blocked\",\"reason\":\"...\"}. '
        "Os campos task_id e objective sao obrigatorios e devem repetir exatamente os valores acima. "
        "Inclua task_id dentro de contract, nunca no objeto externo. Nao use intent como campo. "
        "Use apenas campos aceitos por TaskContract: summary, requirements, constraints, invariants, "
        "success_criteria, out_of_scope, stop_conditions, assumptions e open_questions. "
        "Mantenha as listas concisas, sem repeticoes, preservando requisitos materiais do pedido. "
        "Use listas vazias quando nenhum item for necessario. Nao invente fatos ou perguntas "
        "que a inspecao do workspace possa resolver. "
        'Opcionalmente proponha "verification_checks" simples somente quando forem diretamente fieis '
        "a um requirement ou success_criteria. Cada check deve ter check_id, kind, field, index e path. "
        'Use kind "file_exists" sem required_fields, ou kind "json_fields" com required_fields '
        "nao vazio. field deve ser requirements ou success_criteria; index comeca em zero; path deve "
        "ser relativo ao workspace com barras. json_fields verifica somente presenca de chaves no objeto "
        "raiz, sem validar valores ou tipos; file_exists exige arquivo regular. Nunca emita checks invalidos. "
        "A ausencia desses checks e valida."
    )


def _bind_contract_inputs(raw: Any, task_id: str, objective: str) -> Any:
    """Keep caller-owned identity fields canonical when the model echoes a Contract."""
    if isinstance(raw, TaskContractDecision):
        return replace(
            raw,
            contract=replace(raw.contract, task_id=task_id, objective=objective),
        )
    if isinstance(raw, TaskContract):
        return replace(raw, task_id=task_id, objective=objective)
    if not isinstance(raw, Mapping) or raw.get("action") != "define_contract":
        return raw
    contract = raw.get("contract")
    if not isinstance(contract, Mapping):
        return raw
    decision = dict(raw)
    bound_contract = dict(contract)
    bound_contract["task_id"] = task_id
    bound_contract["objective"] = objective
    decision["contract"] = bound_contract
    return decision


def _spec_prompt(contract: TaskContract) -> str:
    return (
        "Expand the admitted Contract into a bounded TaskSpec. Respond only with the closed "
        "TASK_SPEC decision; do not emit prose, tool calls, capability grants, approvals, or an executable Plan.\n"
        f"Canonical Contract: {contract.to_dict()}\n"
        f"Contract version: {contract.version}; Contract digest: {contract.digest()}\n"
        'Allowed success form: {"action":"define_spec","spec":{...}}; blocked form: '
        '{"action":"blocked","reason":"..."}. The Spec must reproduce the exact task_id, '
        "contract_version and contract_digest and contain descriptive phases with requirements, "
        "invariants, acceptance_criteria, evidence_requirements and depends_on."
    )
