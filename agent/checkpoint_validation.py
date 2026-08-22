"""Structural validation for durable checkpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any, NoReturn

from agent.checkpoint_types import CHECKPOINT_SCHEMA_VERSION, CheckpointLoadError
from agent.execution_state import StepStatus


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
            f"versão incompatível ({version!r}); esperada {CHECKPOINT_SCHEMA_VERSION}",
            reason_code="CHECKPOINT_INCOMPATIBLE_SCHEMA",
        )
    objective = data.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        _invalid(path, "checkpoint sem objetivo textual válido")


def _validate_plan(path: Path, data: dict[str, Any]) -> None:
    plan = data.get("plan")
    if not isinstance(plan, list) or any(not isinstance(step, dict) for step in plan):
        _invalid(path, "checkpoint com plano estruturalmente inválido")
    records = data.get("step_records")
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        _invalid(path, "checkpoint sem registros de execução válidos")
    for record in records:
        _validate_step_record(path, record)
    plan_identity = data.get("plan_identity")
    if plan_identity is not None and (
        not isinstance(plan_identity, str) or not plan_identity.strip()
    ):
        _invalid(path, "identidade de plano inválida")
    plan_step = data.get("plan_step", 0)
    if isinstance(plan_step, bool) or not isinstance(plan_step, int) or plan_step < 0:
        _invalid(path, "cursor de plano inválido")
    current_step_id = data.get("current_step_id")
    if current_step_id is not None and not isinstance(current_step_id, str):
        _invalid(path, "identidade do passo atual inválida")


def _validate_semantics(path: Path, data: dict[str, Any]) -> None:
    raw_semantics = data.get("task_semantics")
    if "task_semantics" in data and raw_semantics is not None and not isinstance(raw_semantics, dict):
        _invalid(path, "contrato semântico ausente ou inválido")
    if raw_semantics is not None:
        return
    legacy_keys = ("requested_effects", "executed_effects", "waived_effects")
    if any(key not in data for key in legacy_keys):
        _invalid(
            path,
            "checkpoint antigo sem estado de obrigações migrável",
            reason_code="CHECKPOINT_MIGRATION_AMBIGUOUS",
        )
    for key in legacy_keys + ("prohibited_effects",):
        if key in data and not _string_list(data[key]):
            _invalid(path, f"campo de efeitos inválido: {key}")


def _validate_optional_fields(path: Path, data: dict[str, Any]) -> None:
    terminal = data.get("terminal_disposition")
    if terminal is not None and not isinstance(terminal, str):
        _invalid(path, "disposição terminal inválida")
    for key in ("tool_history", "events", "conversation_history"):
        if key in data and not isinstance(data[key], list):
            _invalid(path, f"campo de histórico inválido: {key}")
    last_result = data.get("last_result")
    if last_result is not None and not isinstance(last_result, dict):
        _invalid(path, "último resultado inválido")
    budget = data.get("budget")
    if budget is not None and not isinstance(budget, dict):
        _invalid(path, "snapshot de orçamento inválido")


def _validate_step_record(path: Path, record: dict[str, Any]) -> None:
    step_id = record.get("step_id")
    if not isinstance(step_id, str) or not step_id.strip():
        _invalid(path, "registro sem step_id textual")
    status = record.get("status", StepStatus.PENDING.value)
    if not isinstance(status, str) or status not in {item.value for item in StepStatus}:
        _invalid(path, f"status de passo inválido: {status!r}")
    attempts = record.get("attempts", 0)
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        _invalid(path, "número de tentativas inválido")
    if not isinstance(record.get("last_error", ""), str):
        _invalid(path, "último erro do passo inválido")


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
