"""Resolve the exact task definition within the current workspace."""

from __future__ import annotations

from typing import Any

from llm_agent.agent.planning.plan_coverage_models import PlanCoverageError
from llm_agent.agent.task_definition.models import TaskDefinitionRef
from llm_agent.agent.task_definition.resolver import TaskContextMaterialization


def active_outcome_definition(orchestrator: Any) -> tuple[TaskContextMaterialization, TaskDefinitionRef]:
    reference = getattr(orchestrator.agent_state, "task_definition_ref", None)
    resolver = getattr(orchestrator, "task_context_resolver", None)
    if not isinstance(reference, TaskDefinitionRef) or resolver is None:
        raise PlanCoverageError("definição ativa indisponível para associações de requisitos")
    materialization = resolver.resolve(reference)
    if not isinstance(materialization, TaskContextMaterialization):
        raise PlanCoverageError("catálogo de requisitos inválido")
    workspace_id = getattr(getattr(orchestrator, "workspace_paths", None), "workspace_id", None)
    if workspace_id is not None and materialization.workspace_id != workspace_id:
        raise PlanCoverageError("catálogo de requisitos pertence a outro workspace")
    if (
        materialization.task_id != reference.task_id
        or materialization.contract_digest != reference.contract_digest
        or materialization.contract_version != reference.contract_version
        or materialization.spec_digest != reference.spec_digest
        or materialization.spec_version != reference.spec_version
        or materialization.phase_id != reference.active_phase_id
        or not reference.is_complete
    ):
        raise PlanCoverageError("catálogo não corresponde à definição ativa exata")
    return materialization, reference
