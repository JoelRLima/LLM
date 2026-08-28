"""Construction-time normalization for immutable planning tool snapshots."""

from __future__ import annotations

from typing import Any

from agent.planning.schema_safety import (
    PlanningSchemaError,
    validate_planning_schema_shape,
    validate_schema_depth,
)
from agent.skills.descriptor import freeze_result_data_schema
from agent.tools.contracts import CancellationSafetyMode, ToolOriginKind, freeze_json_like
from agent.tools.extension_state import validate_extension_id
from agent.tools.provenance import normalize_argument_provenance
from agent.tools.usage_examples import normalize_usage_examples


def normalize_planning_tool(tool: Any, error_type: type[ValueError]) -> None:
    if not isinstance(tool.name, str) or not tool.name.strip():
        raise error_type("PlanningTool requer nome")
    if not isinstance(tool.description, str):
        raise error_type("PlanningTool requer descrição textual")
    try:
        validate_schema_depth(tool.input_schema)
        validate_planning_schema_shape(tool.input_schema)
    except PlanningSchemaError as exc:
        raise error_type(str(exc)) from exc
    try:
        frozen_schema = freeze_json_like(dict(tool.input_schema))
    except RecursionError as exc:
        raise error_type("schema de planning excede a profundidade maxima") from exc
    object.__setattr__(tool, "input_schema", frozen_schema)
    object.__setattr__(tool, "required_capabilities", frozenset(tool.required_capabilities))
    object.__setattr__(
        tool,
        "argument_provenance",
        normalize_argument_provenance(tool.argument_provenance),
    )
    object.__setattr__(
        tool,
        "result_data_schema",
        freeze_result_data_schema(tool.result_data_schema),
    )
    object.__setattr__(
        tool,
        "usage_examples",
        normalize_usage_examples(
            tool.usage_examples,
            schema=tool.input_schema,
            argument_validator=tool.argument_validator,
            error_type=error_type,
        ),
    )
    _normalize_enums(tool)
    _validate_origin(tool, error_type)


def _normalize_enums(tool: Any) -> None:
    if not isinstance(tool.cancellation_safety, CancellationSafetyMode):
        object.__setattr__(
            tool,
            "cancellation_safety",
            CancellationSafetyMode(str(tool.cancellation_safety)),
        )
    if not isinstance(tool.origin_kind, ToolOriginKind):
        object.__setattr__(tool, "origin_kind", ToolOriginKind(str(tool.origin_kind)))


def _validate_origin(tool: Any, error_type: type[ValueError]) -> None:
    extension_id = tool.extension_id
    if tool.origin_kind is ToolOriginKind.EXTENSION:
        if not isinstance(extension_id, str) or not extension_id.strip():
            raise error_type("Tool de extension requer extension_id")
        try:
            validate_extension_id(extension_id)
        except ValueError as exc:
            raise error_type("extension_id inválido") from exc
    elif extension_id is not None:
        raise error_type("Tool builtin não pode conter extension_id")


__all__ = ["normalize_planning_tool"]
