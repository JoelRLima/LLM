"""Canonical observability mode owned at the application boundary."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any


class ObservabilityMode(str, Enum):
    """The only public runtime observation levels.

    Values are lower-case for configuration/JSON stability while
    ``display_name`` exposes the normative upper-case public names.
    """

    NORMAL = "normal"
    VERBOSE = "verbose"
    DEBUG = "debug"
    TRACE = "trace"

    @property
    def display_name(self) -> str:
        return self.name

    @property
    def rank(self) -> int:
        return {
            ObservabilityMode.NORMAL: 0,
            ObservabilityMode.VERBOSE: 1,
            ObservabilityMode.DEBUG: 2,
            ObservabilityMode.TRACE: 3,
        }[self]

    @classmethod
    def parse(cls, value: Any) -> "ObservabilityMode":
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValueError("observability mode must be NORMAL, VERBOSE, DEBUG, or TRACE")
        normalized = value.strip().casefold().replace("-", "_")
        aliases = {
            "normal": cls.NORMAL,
            "default": cls.NORMAL,
            "verbose": cls.VERBOSE,
            "debug": cls.DEBUG,
            "trace": cls.TRACE,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise ValueError(
                "observability mode must be NORMAL, VERBOSE, DEBUG, or TRACE"
            ) from exc

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any] | None,
        *,
        default: "ObservabilityMode | None" = None,
    ) -> "ObservabilityMode":
        fallback = cls.NORMAL if default is None else default
        if config is None or not isinstance(config, Mapping):
            return fallback
        value = config.get("observability_mode", config.get("observation_mode"))
        return fallback if value is None else cls.parse(value)

    def allows_diagnostic(self, minimum_mode: "ObservabilityMode") -> bool:
        """Return whether this capture mode includes a diagnostic record."""

        selected = self if isinstance(self, ObservabilityMode) else ObservabilityMode.parse(self)
        required = (
            minimum_mode
            if isinstance(minimum_mode, ObservabilityMode)
            else ObservabilityMode.parse(minimum_mode)
        )
        return selected.rank >= required.rank


OBSERVABILITY_MODES = (
    ObservabilityMode.NORMAL,
    ObservabilityMode.VERBOSE,
    ObservabilityMode.DEBUG,
    ObservabilityMode.TRACE,
)


def resolve_observability_mode(
    value: Any = None,
    *,
    config: Mapping[str, Any] | None = None,
    default: ObservabilityMode = ObservabilityMode.NORMAL,
) -> ObservabilityMode:
    """Resolve a mode once at an application/interface boundary."""

    if value is not None:
        return ObservabilityMode.parse(value)
    return ObservabilityMode.from_config(config, default=default)


__all__ = [
    "OBSERVABILITY_MODES",
    "ObservabilityMode",
    "resolve_observability_mode",
]
