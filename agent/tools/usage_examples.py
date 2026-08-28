"""Canonical, bounded synthetic usage examples for tool descriptors.

Examples are descriptor metadata, not observations.  They are validated at
the same planning boundary as the descriptor schema and frozen with the
other canonical metadata so they cannot become a mutable side channel.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping, Sequence
from typing import Any, Callable, cast

from agent.planning.schema_safety import validate_planning_schema_shape
from agent.runtime.schema_validation import normalize_argument_schema, validate_schema_arguments
from agent.tools.json_snapshot import freeze_json_like, thaw_json_like

MAX_USAGE_EXAMPLES = 1
MAX_USAGE_EXAMPLE_CHARS = 2_048
MAX_USAGE_EXAMPLE_PURPOSE_CHARS = 256
_USAGE_EXAMPLE_KEYS = frozenset({"args", "purpose"})


def normalize_usage_examples(
    value: Sequence[Mapping[str, Any]] | None,
    *,
    schema: Mapping[str, Any] | None = None,
    argument_validator: Callable[..., None] | None = None,
    error_type: type[ValueError] = ValueError,
) -> tuple[Mapping[str, Any], ...]:
    """Copy, validate, and freeze a descriptor's synthetic examples.

    ``schema`` and ``argument_validator`` are optional because ``SkillSpec``
    is created before the concrete skill instance exists.  The descriptor,
    tool, and planning projections validate again once that contract is
    available.
    """

    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise error_type("usage_examples deve ser uma sequencia")
    if len(value) > MAX_USAGE_EXAMPLES:
        raise error_type("usage_examples permite no maximo um exemplo")

    normalized = [
        _normalize_example(raw, schema, argument_validator, error_type)
        for raw in value
    ]
    return tuple(normalized)


def _normalize_example(
    raw: Mapping[str, Any] | Any,
    schema: Mapping[str, Any] | None,
    argument_validator: Callable[..., None] | None,
    error_type: type[ValueError],
) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise error_type("cada usage_example deve ser um mapping")
    if set(raw) - _USAGE_EXAMPLE_KEYS or "args" not in raw:
        raise error_type("usage_example requer somente args e purpose opcional")
    args = thaw_json_like(raw["args"])
    if not isinstance(args, Mapping):
        raise error_type("usage_example.args deve ser um mapping JSON-like")
    purpose = raw.get("purpose", "")
    if type(purpose) is not str or len(purpose) > MAX_USAGE_EXAMPLE_PURPOSE_CHARS:
        raise error_type("usage_example.purpose excede o limite textual")
    args_copy = dict(args)
    if schema is not None:
        _validate_example_arguments(schema, args_copy, argument_validator, error_type)
    example: dict[str, Any] = {"args": args_copy}
    if purpose:
        example["purpose"] = purpose
    try:
        frozen = freeze_json_like(example)
        encoded = json.dumps(
            frozen,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_snapshot_default,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise error_type("usage_example deve conter somente JSON-like bounded data") from exc
    if len(encoded) > MAX_USAGE_EXAMPLE_CHARS:
        raise error_type("usage_example excede o limite de tamanho")
    return cast(Mapping[str, Any], frozen)


def _validate_example_arguments(
    schema: Mapping[str, Any],
    args: Mapping[str, Any],
    argument_validator: Callable[..., None] | None,
    error_type: type[ValueError],
) -> None:
    try:
        validate_planning_schema_shape(schema)
        validate_schema_arguments(
            normalize_argument_schema(schema),
            dict(args),
            bound_fields=set(),
            planning=True,
        )
        if argument_validator is not None:
            _call_argument_validator(argument_validator, args)
    except (TypeError, ValueError) as exc:
        raise error_type("usage_example.args nao satisfaz o contrato de planning") from exc


def _call_argument_validator(
    validator: Callable[..., None], args: Mapping[str, Any]
) -> None:
    parameters = inspect.signature(validator).parameters
    kwargs: dict[str, Any] = {}
    if "bound_fields" in parameters:
        kwargs["bound_fields"] = frozenset()
    if "planning" in parameters:
        kwargs["planning"] = True
    validator(args, **kwargs)


def _json_snapshot_default(value: Any) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"unsupported JSON snapshot value: {type(value).__name__}")


__all__ = [
    "MAX_USAGE_EXAMPLES",
    "MAX_USAGE_EXAMPLE_CHARS",
    "normalize_usage_examples",
]
