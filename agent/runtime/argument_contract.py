"""Invocation of tool-specific argument invariants.

JSON shape belongs to :mod:`schema_validation`; cross-field rules belong to
the operation contract that implements the tool. This adapter keeps the
calling convention stable for legacy contracts while avoiding tool-name
branching in global parsers.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any


def validate_operation_arguments(
    contract: Any,
    args: Mapping[str, Any],
    *,
    bound_fields: set[str] | None = None,
    planning: bool = False,
) -> None:
    validator = getattr(contract, "validate_arguments", None)
    if not callable(validator):
        return
    parameters = inspect.signature(validator).parameters
    kwargs: dict[str, Any] = {}
    if "bound_fields" in parameters:
        kwargs["bound_fields"] = frozenset(bound_fields or ())
    if "planning" in parameters:
        kwargs["planning"] = planning
    validator(args, **kwargs)


__all__ = ["validate_operation_arguments"]
