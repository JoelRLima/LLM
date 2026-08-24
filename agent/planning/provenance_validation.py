"""Mechanical provenance checks for descriptor-owned planner arguments."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from agent.tools.provenance import ArgumentOrigin
from agent.tools.result_completeness import canonical_completeness

_SYMBOLIC_REFERENCE_PATTERNS = (
    re.compile(r"\$\{[^{}\r\n]*\}"),
    re.compile(r"\$\{"),
    re.compile(r"\{\{[^{}\r\n]*\}\}"),
    re.compile(r"\{\{"),
    re.compile(r"(?<![\w])\$ref(?:\b|[.:/\[\]_-])", re.IGNORECASE),
    re.compile(
        r"(?<![\w])(?:ref|result|previous|prior|step)\([^()\r\n]+\)",
        re.IGNORECASE,
    ),
    re.compile(r"@[\{\[][^{}\[\]\r\n]+[\}\]]"),
    re.compile(r"<<[^<>\r\n]+>>"),
    re.compile(r"\[\[[^\[\]\r\n]+\]\]"),
    re.compile(
        r"(?<![\w])\{(?:step|result|ref|previous|prior|\d+)[^{}\r\n]*\}",
        re.IGNORECASE,
    ),
)

_GROUNDED_CODE_TOKEN = re.compile(r"(?<!\w)(?:[^\W\d]|_)\w*(?:[.:-](?:[^\W\d]|_)\w*)*(?!\w)", re.UNICODE)
_GROUNDED_DELIMITED_LITERAL = re.compile(
    r"(?P<delimiter>`|'|\")(?P<literal>[^`'\"\r\n]+)(?P=delimiter)"
)
_GENERIC_GROUNDED_WORDS = frozenset(
    "a and arquivo arquivos chamada chamadas contains contém definida definido depois ela em "
    "find for from function função is leia neste nos onde or other outro outros palavra pela por "
    "procure project que saber search the this uma usada usado use used where what which with quero".split()
)


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


def validate_planner_arguments(
    args: Mapping[str, Any],
    bound_fields: set[str],
    descriptor: Any,
    objective: str,
    available_observations: Sequence[Mapping[str, Any]],
) -> str | None:
    """Apply provenance and unresolved-reference checks in canonical order."""

    provenance_problem = validate_argument_provenance(
        args=args,
        bound_fields=bound_fields,
        descriptor=descriptor,
        objective=objective,
        available_observations=available_observations,
    )
    if provenance_problem is not None:
        return provenance_problem
    return validate_unresolved_symbolic_arguments(
        args=args,
        objective=objective,
        available_observations=available_observations,
    )


def validate_unresolved_symbolic_arguments(
    *,
    args: Mapping[str, Any],
    objective: str,
    available_observations: Sequence[Mapping[str, Any]],
) -> str | None:
    """Reject model-authored interpolation text before tool dispatch.

    A string is a legitimate literal when it is explicitly present in the
    user objective or is an exact value in a complete prior observation.
    Everything else that matches a model-facing placeholder family is
    unresolved and must not become a concrete tool argument.
    """

    found = _find_unresolved_symbolic_value(
        args, objective=objective, observations=available_observations
    )
    if found is None:
        return None
    path, value, marker = found
    return (
        "Argumento planner contém referência simbólica não resolvida "
        f"em '{path}': {marker} ({value!r})."
    )


def find_unresolved_symbolic_reference(value: Any) -> str | None:
    """Return the prohibited reference marker in one value, if present."""

    if type(value) is not str:
        return None
    for pattern in _SYMBOLIC_REFERENCE_PATTERNS:
        match = pattern.search(value)
        if match is not None:
            return match.group(0)
    return None


def _find_unresolved_symbolic_value(
    value: Any,
    *,
    objective: str,
    observations: Sequence[Mapping[str, Any]],
    path: str = "args",
    depth: int = 0,
) -> tuple[str, str, str] | None:
    if depth > 16:
        return None
    if type(value) is str:
        marker = find_unresolved_symbolic_reference(value)
        if marker is None or value in objective or observation_contains(observations, value):
            return None
        return path, value, marker
    if isinstance(value, Mapping):
        for key, item in value.items():
            found = _find_unresolved_symbolic_value(
                item,
                objective=objective,
                observations=observations,
                path=f"{path}.{key}",
                depth=depth + 1,
            )
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found = _find_unresolved_symbolic_value(
                item,
                objective=objective,
                observations=observations,
                path=f"{path}[{index}]",
                depth=depth + 1,
            )
            if found is not None:
                return found
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


def grounded_user_literal_narrowing(
    *, rejected_value: object, objective: str
) -> str | None:
    """Narrow only to an exact literal sourced from the user objective."""

    if type(rejected_value) is not str or not rejected_value or type(objective) is not str:
        return None
    explicit_candidates: list[str] = []
    bare_candidates: list[str] = []

    def add_candidate(raw: str, destination: list[str]) -> None:
        candidate = raw
        if (
            len(candidate) < 3
            or not candidate.strip()
            or not any(char.isalnum() or char == "_" for char in candidate)
            or (destination is bare_candidates and candidate.casefold() in _GENERIC_GROUNDED_WORDS)
            or candidate not in objective
            or candidate not in rejected_value
        ):
            return
        if candidate not in destination:
            destination.append(candidate)
    delimited = tuple(_GROUNDED_DELIMITED_LITERAL.finditer(objective))
    for match in delimited:
        add_candidate(match.group("literal"), explicit_candidates)
    for match in _GROUNDED_CODE_TOKEN.finditer(objective):
        if any(match.start() < item.end() and item.start() < match.end() for item in delimited):
            continue
        add_candidate(match.group(0), bare_candidates)

    if len(explicit_candidates) > 1:
        return None
    candidates = explicit_candidates or bare_candidates
    return candidates[0] if len(candidates) == 1 else None


__all__ = ["find_unresolved_symbolic_reference", "grounded_user_literal_narrowing", "observation_contains", "provenance_error", "validate_argument_provenance", "validate_planner_arguments", "validate_unresolved_symbolic_arguments", "value_contains"]
