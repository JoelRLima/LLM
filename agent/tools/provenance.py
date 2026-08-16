"""Small descriptor-owned argument provenance contracts.

The planner may synthesize ordinary arguments, but selected arguments can
declare a narrower set of mechanically checkable origins.  This module is a
value contract only; validation remains at the planning boundary.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import Enum
from types import MappingProxyType


class ArgumentOrigin(str, Enum):
    """Origins that the runtime can establish without an LLM judge."""

    USER_LITERAL = "user_literal"
    OBSERVATION_LITERAL = "observation_literal"
    RESULT_BINDING = "result_binding"


def normalize_argument_provenance(
    value: Mapping[str, Iterable[str | ArgumentOrigin]] | None,
) -> Mapping[str, frozenset[str]]:
    """Copy and validate a small descriptor provenance declaration."""

    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise TypeError("argument_provenance deve ser um mapping")
    normalized: dict[str, frozenset[str]] = {}
    for argument, origins in value.items():
        if type(argument) is not str or not argument.strip():
            raise ValueError("argument_provenance requer nomes de argumentos validos")
        if isinstance(origins, (str, ArgumentOrigin)):
            raise TypeError("origens de provenance devem ser uma colecao")
        try:
            origin_values = frozenset(
                item.value if isinstance(item, ArgumentOrigin) else item
                for item in origins
            )
        except TypeError as exc:
            raise TypeError("origens de provenance devem ser uma colecao") from exc
        if not origin_values or any(
            type(item) is not str or item not in {origin.value for origin in ArgumentOrigin}
            for item in origin_values
        ):
            raise ValueError("origem de provenance desconhecida")
        normalized[argument] = origin_values
    return MappingProxyType(normalized)


__all__ = ["ArgumentOrigin", "normalize_argument_provenance"]
