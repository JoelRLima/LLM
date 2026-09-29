"""Neutral, immutable contracts for local-first command discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping, cast

MAX_DISCOVERY_QUERY_CHARS = 512
MAX_DISCOVERY_RESULTS = 20
MAX_DISCOVERY_SEMANTIC_CANDIDATES = 64
MAX_DISCOVERY_ENTRY_ID_BYTES = 192
MAX_DISCOVERY_TITLE_CHARS = 128
MAX_DISCOVERY_DESCRIPTION_CHARS = 512
MAX_DISCOVERY_INVOCATION_CHARS = 512
MAX_DISCOVERY_ALIAS_COUNT = 16
MAX_DISCOVERY_ALIAS_CHARS = 192
MAX_DISCOVERY_KEYWORD_COUNT = 24
MAX_DISCOVERY_KEYWORD_CHARS = 96
MAX_DISCOVERY_EXAMPLE_COUNT = 8
MAX_DISCOVERY_EXAMPLE_CHARS = 256
DISCOVERY_QUERY_EMPTY = "DISCOVERY_QUERY_EMPTY"
DISCOVERY_NO_MATCH = "DISCOVERY_NO_MATCH"
DISCOVERY_AVAILABILITY_UNKNOWN = "DISCOVERY_AVAILABILITY_UNKNOWN"
DISCOVERY_SEMANTIC_NOT_AUTHORIZED = "DISCOVERY_SEMANTIC_NOT_AUTHORIZED"
DISCOVERY_SEMANTIC_UNAVAILABLE = "DISCOVERY_SEMANTIC_UNAVAILABLE"
DISCOVERY_SEMANTIC_PAYLOAD_TOO_LARGE = "DISCOVERY_SEMANTIC_PAYLOAD_TOO_LARGE"
DISCOVERY_SEMANTIC_INVALID_RESPONSE = "DISCOVERY_SEMANTIC_INVALID_RESPONSE"
DISCOVERY_SEMANTIC_USED = "DISCOVERY_SEMANTIC_USED"
DISCOVERY_REQUIRES_IDLE = "DISCOVERY_REQUIRES_IDLE"
DISCOVERY_REQUIRES_ATTENTION = "DISCOVERY_REQUIRES_ATTENTION"
DISCOVERY_REASON_ORDER = (
    DISCOVERY_QUERY_EMPTY,
    DISCOVERY_NO_MATCH,
    DISCOVERY_AVAILABILITY_UNKNOWN,
    DISCOVERY_SEMANTIC_NOT_AUTHORIZED,
    DISCOVERY_SEMANTIC_UNAVAILABLE,
    DISCOVERY_SEMANTIC_PAYLOAD_TOO_LARGE,
    DISCOVERY_SEMANTIC_INVALID_RESPONSE,
    DISCOVERY_SEMANTIC_USED,
)
_REASON_INDEX = {value: index for index, value in enumerate(DISCOVERY_REASON_ORDER)}
_STABLE_REASON = re.compile(r"[A-Z][A-Z0-9_]{2,127}")


class DiscoverySourceKind(str, Enum):
    ACTION = "action"
    CLI_COMMAND = "cli_command"
    ENGINEERING_OPERATION = "engineering_operation"


class DiscoveryControllerState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    WAITING_ATTENTION = "WAITING_ATTENTION"
    CANCELLING = "CANCELLING"
    SETTLING = "SETTLING"
    TERMINAL = "TERMINAL"


class DiscoveryMatchKind(str, Enum):
    EXACT = "exact"
    PREFIX = "prefix"
    TOKEN = "token"
    SUBSTRING = "substring"
    FUZZY = "fuzzy"
    NONE = "none"


def _text(value: object, label: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be text")
    if required and not value.strip():
        raise ValueError(f"{label} must be non-empty")
    if len(value) > maximum:
        raise ValueError(f"{label} exceeds its bound")
    return value


def _tuple_text(values: object, label: str, maximum_items: int, maximum_chars: int) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise TypeError(f"{label} must be a sequence of strings")
    try:
        result: tuple[object, ...] = tuple(cast(Iterable[object], values))
    except TypeError as exc:
        raise TypeError(f"{label} must be a sequence of strings") from exc
    if len(result) > maximum_items:
        raise ValueError(f"{label} exceeds its item bound")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in result:
        value = _text(item, label, maximum_chars)
        key = value.casefold()
        if key in seen:
            raise ValueError(f"duplicate {label} value")
        seen.add(key)
        normalized.append(value)
    return tuple(normalized)


def _validate_entry_identity(schema_version: int, entry_id: str, source_kind: DiscoverySourceKind) -> None:
    if isinstance(schema_version, bool) or schema_version != 1:
        raise ValueError("unsupported discovery schema")
    _text(entry_id, "entry_id", MAX_DISCOVERY_ENTRY_ID_BYTES)
    if len(entry_id.encode("utf-8")) > MAX_DISCOVERY_ENTRY_ID_BYTES:
        raise ValueError("entry_id exceeds its UTF-8 bound")
    expected_prefix = {
        DiscoverySourceKind.ACTION: "action:",
        DiscoverySourceKind.CLI_COMMAND: "cli:",
        DiscoverySourceKind.ENGINEERING_OPERATION: "engineering:",
    }.get(source_kind)
    if expected_prefix is None:
        raise TypeError("source_kind must be DiscoverySourceKind")
    if not entry_id.startswith(expected_prefix):
        raise ValueError("entry_id does not match source kind")


def _normalize_entry_fields(entry: "DiscoveryEntryV1") -> None:
    fields = (
        ("aliases", entry.aliases, MAX_DISCOVERY_ALIAS_COUNT, MAX_DISCOVERY_ALIAS_CHARS),
        ("keywords", entry.keywords, MAX_DISCOVERY_KEYWORD_COUNT, MAX_DISCOVERY_KEYWORD_CHARS),
        ("examples", entry.examples, MAX_DISCOVERY_EXAMPLE_COUNT, MAX_DISCOVERY_EXAMPLE_CHARS),
    )
    for label, values, maximum_items, maximum_chars in fields:
        object.__setattr__(entry, label, _tuple_text(values, label, maximum_items, maximum_chars))


@dataclass(frozen=True, slots=True)
class DiscoveryEntryV1:
    schema_version: int
    entry_id: str
    source_kind: DiscoverySourceKind
    title: str
    description: str
    preferred_invocation: str
    aliases: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_entry_identity(self.schema_version, self.entry_id, self.source_kind)
        for value, label, maximum in (
            (self.title, "title", MAX_DISCOVERY_TITLE_CHARS),
            (self.description, "description", MAX_DISCOVERY_DESCRIPTION_CHARS),
            (self.preferred_invocation, "preferred_invocation", MAX_DISCOVERY_INVOCATION_CHARS),
        ):
            _text(value, label, maximum)
        _normalize_entry_fields(self)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "entry_id": self.entry_id,
            "source_kind": self.source_kind.value,
            "title": self.title,
            "description": self.description,
            "preferred_invocation": self.preferred_invocation,
            "aliases": list(self.aliases),
            "keywords": list(self.keywords),
            "examples": list(self.examples),
        }


@dataclass(frozen=True, slots=True)
class DiscoveryAvailability:
    available: bool
    disabled_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.available, bool):
            raise TypeError("available must be bool")
        if self.disabled_reason is not None:
            _text(self.disabled_reason, "disabled_reason", 128)
            if _STABLE_REASON.fullmatch(self.disabled_reason) is None:
                raise ValueError("disabled_reason must be a stable reason code")
        if self.available and self.disabled_reason is not None:
            raise ValueError("available entries cannot carry disabled_reason")

    def to_dict(self) -> dict[str, object]:
        return {"available": self.available, "disabled_reason": self.disabled_reason}

@dataclass(frozen=True, slots=True)
class DiscoveryExecutionContext:
    controller_state: DiscoveryControllerState | None = None
    workspace_bound: bool = False
    availability_by_entry_id: Mapping[str, DiscoveryAvailability] = MappingProxyType({})

    def __post_init__(self) -> None:
        if self.controller_state is not None and not isinstance(self.controller_state, DiscoveryControllerState):
            raise TypeError("controller_state must be DiscoveryControllerState or None")
        if not isinstance(self.workspace_bound, bool):
            raise TypeError("workspace_bound must be bool")
        values: dict[str, DiscoveryAvailability] = {}
        for key, value in self.availability_by_entry_id.items():
            if not isinstance(key, str) or not isinstance(value, DiscoveryAvailability):
                raise TypeError("availability map is invalid")
            values[key] = value
        object.__setattr__(self, "availability_by_entry_id", MappingProxyType(values))


@dataclass(frozen=True, slots=True)
class DiscoveryCandidateV1:
    entry: DiscoveryEntryV1
    available: bool
    disabled_reason: str | None
    match_kind: DiscoveryMatchKind
    local_score: Mapping[str, int]
    frecency_score: int
    semantic_selected: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.entry, DiscoveryEntryV1):
            raise TypeError("candidate entry is invalid")
        if not isinstance(self.available, bool):
            raise TypeError("candidate availability is invalid")
        if self.disabled_reason is not None:
            _text(self.disabled_reason, "disabled_reason", 128)
            if _STABLE_REASON.fullmatch(self.disabled_reason) is None:
                raise ValueError("disabled_reason must be a stable reason code")
        if self.available and self.disabled_reason is not None:
            raise ValueError("available candidates cannot carry disabled_reason")
        if not isinstance(self.match_kind, DiscoveryMatchKind):
            raise TypeError("candidate match kind is invalid")
        if isinstance(self.frecency_score, bool) or not isinstance(self.frecency_score, int) or not 0 <= self.frecency_score <= 100:
            raise ValueError("candidate frecency score is invalid")
        if not isinstance(self.semantic_selected, bool):
            raise TypeError("semantic_selected must be bool")
        object.__setattr__(self, "local_score", normalize_local_score(self.local_score))

    def to_dict(self) -> dict[str, object]:
        return {
            "entry": self.entry.to_dict(),
            "available": self.available,
            "disabled_reason": self.disabled_reason,
            "match_kind": self.match_kind.value,
            "local_score": dict(self.local_score),
            "frecency_score": self.frecency_score,
            "semantic_selected": self.semantic_selected,
        }


@dataclass(frozen=True, slots=True)
class DiscoveryResultV1:
    query: str
    candidates: tuple[DiscoveryCandidateV1, ...]
    reasons: tuple[str, ...]
    semantic_requested: bool
    semantic_used: bool

    def __post_init__(self) -> None:
        _text(self.query, "query", MAX_DISCOVERY_QUERY_CHARS, required=False)
        if len(self.candidates) > MAX_DISCOVERY_RESULTS:
            raise ValueError("too many discovery candidates")
        if not all(isinstance(item, DiscoveryCandidateV1) for item in self.candidates):
            raise TypeError("invalid discovery candidate")
        if not isinstance(self.semantic_requested, bool) or not isinstance(self.semantic_used, bool):
            raise TypeError("invalid semantic flags")
        object.__setattr__(self, "reasons", normalize_reasons(self.reasons))

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "candidates": [item.to_dict() for item in self.candidates],
            "reasons": list(self.reasons),
            "semantic_requested": self.semantic_requested,
            "semantic_used": self.semantic_used,
        }


def normalize_reasons(values: object) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)):
        raise TypeError("discovery reasons must be a sequence")
    try:
        unique: set[object] = set(cast(Iterable[object], values))
    except TypeError as exc:
        raise TypeError("discovery reasons must be a sequence") from exc
    if any(not isinstance(value, str) or value not in _REASON_INDEX for value in unique):
        raise ValueError("unknown discovery reason")
    reasons = (value for value in unique if isinstance(value, str))
    return tuple(sorted(reasons, key=_REASON_INDEX.__getitem__))


def availability_for(entry: DiscoveryEntryV1, context: DiscoveryExecutionContext) -> DiscoveryAvailability:
    value = context.availability_by_entry_id.get(entry.entry_id)
    if value is not None:
        return value
    return DiscoveryAvailability(False, DISCOVERY_AVAILABILITY_UNKNOWN)

def normalize_local_score(values: Mapping[str, int]) -> Mapping[str, int]:
    """Freeze the bounded local score projection without assigning meaning."""
    projected: dict[str, int] = {}
    for key, value in values.items():
        if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("local score is invalid")
        projected[key] = value
    return MappingProxyType(projected)

__all__ = ("DISCOVERY_AVAILABILITY_UNKNOWN", "DISCOVERY_NO_MATCH", "DISCOVERY_QUERY_EMPTY", "DISCOVERY_REASON_ORDER", "DISCOVERY_REQUIRES_ATTENTION", "DISCOVERY_REQUIRES_IDLE", "DISCOVERY_SEMANTIC_INVALID_RESPONSE", "DISCOVERY_SEMANTIC_NOT_AUTHORIZED", "DISCOVERY_SEMANTIC_PAYLOAD_TOO_LARGE", "DISCOVERY_SEMANTIC_UNAVAILABLE", "DISCOVERY_SEMANTIC_USED", "DiscoveryAvailability", "DiscoveryCandidateV1", "DiscoveryControllerState", "DiscoveryEntryV1", "DiscoveryExecutionContext", "DiscoveryMatchKind", "DiscoveryResultV1", "DiscoverySourceKind", "MAX_DISCOVERY_QUERY_CHARS", "MAX_DISCOVERY_RESULTS", "MAX_DISCOVERY_SEMANTIC_CANDIDATES", "availability_for", "normalize_local_score", "normalize_reasons")
