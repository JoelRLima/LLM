"""Mechanical provenance checks for descriptor-owned planner arguments."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.tools.provenance import ArgumentOrigin
from agent.tools.result_completeness import canonical_completeness


def validate_argument_provenance(
    *,
    args: Mapping[str, Any],
    bound_fields: set[str],
    descriptor: Any,
    objective: str,
    available_observations: Sequence[Mapping[str, Any]],
) -> str | None:
    """Return a bounded validation error for a protected argument."""

    policies = getattr(descriptor, "argument_provenance", None)
    if policies is None:
        policies = getattr(getattr(descriptor, "spec", None), "argument_provenance", None)
    if not policies:
        return None
    for argument, allowed in policies.items():
        if argument in bound_fields and ArgumentOrigin.RESULT_BINDING.value in allowed:
            continue
        if argument not in args:
            return provenance_error(argument, allowed)
        value = args.get(argument)
        if _user_literal_allowed(value, allowed, objective):
            continue
        if _observation_literal_allowed(value, allowed, available_observations):
            continue
        return provenance_error(argument, allowed)
    return None


def provenance_error(argument: str, allowed: Any) -> str:
    labels = {
        ArgumentOrigin.USER_LITERAL.value: "valor exato fornecido pelo usuario",
        ArgumentOrigin.OBSERVATION_LITERAL.value: "valor exato de uma observacao canonica disponivel",
        ArgumentOrigin.RESULT_BINDING.value: "binding do resultado de um passo anterior",
    }
    allowed_labels = [labels.get(str(origin), str(origin)) for origin in sorted(allowed)]
    return (
        f"Argumento '{argument}' requer proveniencia fundamentada; permitidos: "
        + "; ".join(allowed_labels)
        + "."
    )


def _user_literal_allowed(value: Any, allowed: Any, objective: str) -> bool:
    return (
        ArgumentOrigin.USER_LITERAL.value in allowed
        and type(value) is str
        and bool(value)
        and value in objective
    )


def _observation_literal_allowed(
    value: Any,
    allowed: Any,
    observations: Sequence[Mapping[str, Any]],
) -> bool:
    return (
        ArgumentOrigin.OBSERVATION_LITERAL.value in allowed
        and type(value) is str
        and bool(value)
        and observation_contains(observations, value)
    )


def observation_contains(
    observations: Sequence[Mapping[str, Any]], candidate: str
) -> bool:
    for item in observations:
        if not isinstance(item, Mapping):
            continue
        raw_result = item.get("result")
        result = _result_mapping(raw_result, item)
        if not _complete_successful_result(result):
            continue
        if value_contains(result.get("data"), candidate):
            return True
    return False


def _result_mapping(raw_result: Any, fallback: Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(raw_result, Mapping):
        return raw_result
    to_legacy = getattr(raw_result, "to_legacy_dict", None)
    if callable(to_legacy):
        try:
            result = to_legacy(include_details=True)
        except (TypeError, ValueError):
            result = None
        if isinstance(result, Mapping):
            return result
    return fallback


def _complete_successful_result(result: Mapping[str, Any]) -> bool:
    return (
        result.get("ok") is True
        and result.get("executed") is True
        and result.get("status") == "succeeded"
        and "data" in result
        and canonical_completeness(result)[0]
    )


def value_contains(value: Any, candidate: str, depth: int = 0) -> bool:
    if depth > 16:
        return False
    if type(value) is str:
        return value == candidate
    if isinstance(value, Mapping):
        return any(value_contains(item, candidate, depth + 1) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(value_contains(item, candidate, depth + 1) for item in value)
    return False


__all__ = [
    "observation_contains",
    "provenance_error",
    "validate_argument_provenance",
    "value_contains",
]
