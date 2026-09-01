"""Structural validation for durable checkpoints."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, NoReturn

from agent.checkpoint_types import CHECKPOINT_SCHEMA_VERSION, CheckpointLoadError
from agent.execution_incidents import normalize_execution_incidents
from agent.execution_state import StepStatus
from agent.task_definition.models import TaskDefinitionRef


def validate_document(path: Path, data: Any) -> None:
    if not isinstance(data, dict):
        _invalid(path, "a raiz deve ser um objeto")
    _validate_header(path, data)
    _validate_plan(path, data)
    _validate_semantics(path, data)
    _validate_optional_fields(path, data)


def _validate_header(path: Path, data: dict[str, Any]) -> None:
    version = data.get("schema_version")
    if isinstance(version, bool) or version != CHECKPOINT_SCHEMA_VERSION:
        _invalid(
            path,
            f"versao incompativel ({version!r}); esperada {CHECKPOINT_SCHEMA_VERSION}",
            reason_code="CHECKPOINT_INCOMPATIBLE_SCHEMA",
        )
    objective = data.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        _invalid(path, "checkpoint sem objetivo textual valido")


def _validate_plan(path: Path, data: dict[str, Any]) -> None:
    plan = data.get("plan")
    if not isinstance(plan, list) or any(not isinstance(step, dict) for step in plan):
        _invalid(path, "checkpoint com plano estruturalmente invalido")
    if _contains_retired_tool_alias(plan):
        _invalid(
            path,
            "checkpoint contém o alias de ferramenta aposentado: git; use git_reader",
            reason_code="W7_RETIRED_TOOL_ALIAS",
        )
    records = data.get("step_records")
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        _invalid(path, "checkpoint sem registros de execucao validos")
    for record in records:
        _validate_step_record(path, record)
    plan_identity = data.get("plan_identity")
    if plan_identity is not None and (
        not isinstance(plan_identity, str) or not plan_identity.strip()
    ):
        _invalid(path, "identidade de plano invalida")
    plan_step = data.get("plan_step", 0)
    if isinstance(plan_step, bool) or not isinstance(plan_step, int) or plan_step < 0:
        _invalid(path, "cursor de plano invalido")
    current_step_id = data.get("current_step_id")
    if current_step_id is not None and not isinstance(current_step_id, str):
        _invalid(path, "identidade do passo atual invalida")


def _validate_semantics(path: Path, data: dict[str, Any]) -> None:
    raw_semantics = data.get("task_semantics")
    if "task_semantics" in data and raw_semantics is not None and not isinstance(raw_semantics, dict):
        _invalid(path, "contrato semantico ausente ou invalido")
    if raw_semantics is not None:
        return
    legacy_keys = ("requested_effects", "executed_effects", "waived_effects")
    if any(key not in data for key in legacy_keys):
        _invalid(
            path,
            "checkpoint antigo sem estado de obrigacoes migravel",
            reason_code="CHECKPOINT_MIGRATION_AMBIGUOUS",
        )
    for key in legacy_keys + ("prohibited_effects",):
        if key in data and not _string_list(data[key]):
            _invalid(path, f"campo de efeitos invalido: {key}")


def _validate_optional_fields(path: Path, data: dict[str, Any]) -> None:
    _validate_task_definition_binding(path, data)
    _validate_root_and_terminal(path, data)
    _validate_history_fields(path, data)
    _validate_incidents(path, data)
    _validate_result_and_budget(path, data)
    _validate_task_policy(path, data)
    _validate_hierarchical_lifecycle(path, data)


def _validate_task_definition_binding(path: Path, data: dict[str, Any]) -> None:
    raw_task_definition = data.get("task_definition")
    if raw_task_definition is None:
        return
    if not isinstance(raw_task_definition, dict):
        _invalid(path, "binding de task definition invalido")
    try:
        task_definition_ref = TaskDefinitionRef.from_dict(raw_task_definition)
    except (TypeError, ValueError):
        _invalid(path, "binding de task definition invalido")
    if data.get("root_task_id") != task_definition_ref.task_id:
        _invalid(path, "binding de task definition nao corresponde a tarefa raiz")


def _validate_root_and_terminal(path: Path, data: dict[str, Any]) -> None:
    root_task_id = data.get("root_task_id")
    if root_task_id is not None and (
        not isinstance(root_task_id, str) or not root_task_id.strip()
    ):
        _invalid(path, "identidade de tarefa raiz invalida")
    terminal = data.get("terminal_disposition")
    if terminal is not None and not isinstance(terminal, str):
        _invalid(path, "disposicao terminal invalida")


def _validate_history_fields(path: Path, data: dict[str, Any]) -> None:
    for key in ("tool_history", "events", "conversation_history"):
        if key in data and not isinstance(data[key], list):
            _invalid(path, f"campo de historico invalido: {key}")


def _validate_incidents(path: Path, data: dict[str, Any]) -> None:
    if "execution_incidents" not in data:
        return
    try:
        normalize_execution_incidents(data["execution_incidents"])
    except (TypeError, ValueError):
        _invalid(path, "diario de incidentes de execucao invalido")


def _validate_result_and_budget(path: Path, data: dict[str, Any]) -> None:
    last_result = data.get("last_result")
    if last_result is not None and not isinstance(last_result, dict):
        _invalid(path, "ultimo resultado invalido")
    budget = data.get("budget")
    if budget is not None and not isinstance(budget, dict):
        _invalid(path, "snapshot de orcamento invalido")


def _validate_task_policy(path: Path, data: dict[str, Any]) -> None:
    policy = data.get("task_policy")
    if policy is None:
        return
    if not isinstance(policy, dict):
        _invalid(path, "estado de politica da tarefa invalido")
    retired = sorted(
        key
        for key in ("consumed_logical_steps", "active_elapsed")
        if key in policy
    )
    if retired:
        _invalid(
            path,
            "checkpoint task policy contém nome W6 aposentado: " + ",".join(retired),
            reason_code="W7_RETIRED_TASK_POLICY_KEY",
        )
    if "logical_work_units_consumed" not in policy or "active_elapsed_seconds" not in policy:
        _invalid(
            path,
            "checkpoint task policy exige nomes canônicos W6",
            reason_code="W7_CANONICAL_TASK_POLICY_KEYS_REQUIRED",
        )
    consumed = policy["logical_work_units_consumed"]
    elapsed = policy["active_elapsed_seconds"]
    if isinstance(consumed, bool) or not isinstance(consumed, int) or consumed < 0:
        _invalid(path, "contador de unidades logicas invalido")
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(float(elapsed))
        or elapsed < 0
    ):
        _invalid(path, "duracao ativa acumulada invalida")


def _contains_retired_tool_alias(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("tool") == "git":
            return True
        return any(_contains_retired_tool_alias(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_retired_tool_alias(item) for item in value)
    return False


def _validate_hierarchical_lifecycle(path: Path, data: dict[str, Any]) -> None:
    lifecycle = data.get("hierarchical_lifecycle")
    if lifecycle is None:
        return
    if not isinstance(lifecycle, dict):
        _invalid(path, "ciclo de vida hierarquico invalido")
    if lifecycle.get("status", "inactive") not in {"inactive", "running", "completed"}:
        _invalid(path, "status de ciclo de vida hierarquico invalido")


def _validate_step_record(path: Path, record: dict[str, Any]) -> None:
    step_id = record.get("step_id")
    if not isinstance(step_id, str) or not step_id.strip():
        _invalid(path, "registro sem step_id textual")
    status = record.get("status", StepStatus.PENDING.value)
    if not isinstance(status, str) or status not in {item.value for item in StepStatus}:
        _invalid(path, f"status de passo invalido: {status!r}")
    attempts = record.get("attempts", 0)
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        _invalid(path, "numero de tentativas invalido")
    if not isinstance(record.get("last_error", ""), str):
        _invalid(path, "ultimo erro do passo invalido")


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _invalid(
    path: Path,
    detail: str,
    *,
    reason_code: str = "CHECKPOINT_INVALID",
) -> NoReturn:
    raise CheckpointLoadError(path, detail, reason_code=reason_code)


__all__ = ["validate_document"]
