"""Immutable occurrence-level failure facts.

Error definitions remain owned by :mod:`agent.runtime.outcome_taxonomy` and
its authored registry.  This module records one observed failure and exposes
safe projections; it does not author a second code table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent.runtime.errors import ToolNotFoundError
from agent.runtime.outcome_taxonomy import (
    HARD_FAILURE_STATUSES,
    ErrorDefinition,
    FailureLayer,
    OperationalStatus,
    error_definition,
    operational_status_for,
)
from agent.tools.contracts import ToolResult
from agent.tools.json_snapshot import FrozenJsonObject, freeze_json_like

UNKNOWN_FAILURE_CODE = "UNKNOWN_FAILURE"
_MAX_MESSAGE_CHARS = 600
def _bounded_message(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:_MAX_MESSAGE_CHARS] if text else None
def _freeze_detail(value: Any) -> FrozenJsonObject | None:
    if not isinstance(value, Mapping):
        return None
    try:
        frozen = freeze_json_like(dict(value))
    except (TypeError, ValueError, RecursionError):
        # A malformed detail payload must never become a mutable side channel.
        return None
    return frozen if isinstance(frozen, FrozenJsonObject) else None
def _normalized_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    code = value.strip()
    return code or None
def _status_or_default(value: Any, definition: ErrorDefinition | None) -> str:
    status = operational_status_for(value)
    if status is not None:
        return status
    if definition is not None:
        default = operational_status_for(definition.default_status)
        if default is not None:
            return default
    return OperationalStatus.FAILED.value
@dataclass(frozen=True, slots=True)
class FailureFact:
    """One immutable, occurrence-level failure fact.

    ``message`` and ``detail`` are diagnostic data only.  Recovery callers
    branch on ``code``, ``layer``, ``status``, ``retryable`` and ``hard``.
    """

    code: str
    layer: FailureLayer
    status: str
    retryable: bool
    hard: bool
    message: str | None = None
    detail: FrozenJsonObject | None = None
    invocation_id: str | None = None
    tool_name: str | None = None
    step_id: str | None = None

    def __post_init__(self) -> None:
        code = _normalized_code(self.code) or UNKNOWN_FAILURE_CODE
        status = operational_status_for(self.status) or OperationalStatus.FAILED.value
        definition = error_definition(code)
        layer = (
            definition.layer
            if definition is not None
            else FailureLayer.RUNTIME
        )
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "layer", layer)
        object.__setattr__(
            self,
            "retryable",
            bool(definition is not None and definition.retryable)
            and status not in HARD_FAILURE_STATUSES,
        )
        object.__setattr__(
            self,
            "hard",
            (definition.hard if definition is not None else bool(self.hard))
            or status in HARD_FAILURE_STATUSES,
        )
        object.__setattr__(self, "message", _bounded_message(self.message))
        object.__setattr__(self, "invocation_id", _bounded_message(self.invocation_id))
        object.__setattr__(self, "tool_name", _bounded_message(self.tool_name))
        object.__setattr__(self, "step_id", _bounded_message(self.step_id))

    @property
    def error_code(self) -> str:
        """Compatibility spelling for callers that name the code explicitly."""

        return self.code

    @property
    def failure_layer(self) -> FailureLayer:
        return self.layer

    @property
    def operational_status(self) -> str:
        return self.status

    @property
    def is_retryable(self) -> bool:
        return self.retryable

    @property
    def is_hard(self) -> bool:
        return self.hard

    @property
    def definition(self) -> ErrorDefinition | None:
        """Return the registry definition, if this code is registered."""

        return error_definition(self.code)

    @property
    def public_message(self) -> str:
        definition = self.definition
        if definition is None or not definition.public_safe:
            return "Falha operacional não detalhada."
        # A public-safe code only makes the stable definition safe to name. It
        # does not certify occurrence diagnostics supplied by a tool/provider.
        return f"Falha operacional ({self.code})."

    def to_public_dict(self) -> dict[str, Any]:
        """Project bounded diagnostics without exposing unsafe raw detail."""

        definition = self.definition
        public: dict[str, Any] = {
            "status": self.status,
            "layer": self.layer.value,
            "message": self.public_message,
        }
        if definition is not None and definition.public_safe:
            public["error_code"] = self.code
        return public

    def to_dict(self, *, public: bool = False) -> dict[str, Any]:
        """Return either an internal diagnostic or a safe public projection."""

        if public:
            return self.to_public_dict()
        result: dict[str, Any] = {
            "error_code": self.code,
            "layer": self.layer.value,
            "status": self.status,
            "retryable": self.retryable,
            "hard": self.hard,
        }
        if self.message is not None:
            result["message"] = self.message
        if self.detail is not None:
            result["detail"] = dict(self.detail)
        for key, value in (
            ("invocation_id", self.invocation_id),
            ("tool_name", self.tool_name),
            ("step_id", self.step_id),
        ):
            if value is not None:
                result[key] = value
        return result

    @classmethod
    def from_code(
        cls,
        code: str | None,
        *,
        status: str | OperationalStatus | None = None,
        message: str | None = None,
        detail: Mapping[str, Any] | None = None,
        invocation_id: str | None = None,
        tool_name: str | None = None,
        step_id: str | None = None,
    ) -> "FailureFact":
        normalized = _normalized_code(code) or UNKNOWN_FAILURE_CODE
        definition = error_definition(normalized)
        occurrence_status = _status_or_default(status, definition)
        return cls(
            code=normalized,
            layer=definition.layer if definition is not None else FailureLayer.RUNTIME,
            status=occurrence_status,
            retryable=definition.retryable if definition is not None else False,
            hard=(definition.hard if definition is not None else False)
            or occurrence_status in HARD_FAILURE_STATUSES,
            message=message,
            detail=_freeze_detail(detail),
            invocation_id=invocation_id,
            tool_name=tool_name,
            step_id=step_id,
        )

    @classmethod
    def unknown(
        cls,
        *,
        status: str | OperationalStatus | None = None,
        message: str | None = None,
        detail: Mapping[str, Any] | None = None,
        invocation_id: str | None = None,
        tool_name: str | None = None,
        step_id: str | None = None,
    ) -> "FailureFact":
        return cls.from_code(
            UNKNOWN_FAILURE_CODE,
            status=status,
            message=message,
            detail=detail,
            invocation_id=invocation_id,
            tool_name=tool_name,
            step_id=step_id,
        )

    @classmethod
    def from_tool_result(
        cls,
        result: ToolResult,
        *,
        tool_name: str | None = None,
        step_id: str | None = None,
    ) -> "FailureFact | None":
        """Build a fact from typed fields; a successful result yields ``None``."""

        if not isinstance(result, ToolResult):
            raise TypeError("FailureFact.from_tool_result requires ToolResult")
        status = operational_status_for(result.status)
        if status == OperationalStatus.SUCCEEDED.value:
            return None
        error = result.error
        code = _normalized_code(error.code if error is not None else None)
        message = (
            error.message if error is not None else result.message
        )
        detail = error.detail if error is not None else None
        fact = cls.from_code(
            code,
            status=status,
            message=message,
            detail=detail,
            invocation_id=result.invocation_id,
            tool_name=tool_name,
            step_id=step_id,
        )
        return fact

    @classmethod
    def from_exception(
        cls,
        error: BaseException,
        *,
        invocation_id: str | None = None,
        tool_name: str | None = None,
        step_id: str | None = None,
    ) -> "FailureFact":
        """Map only stable exception types used by the runtime."""

        code: str | None = None
        if isinstance(error, FileNotFoundError):
            code = "FILE_NOT_FOUND"
        elif isinstance(error, PermissionError):
            code = "PERMISSION_DENIED"
        elif isinstance(error, TimeoutError):
            code = "TIMEOUT"
        elif isinstance(error, ToolNotFoundError):
            code = "TOOL_NOT_FOUND"
        return cls.from_code(
            code,
            message=str(error),
            invocation_id=invocation_id,
            tool_name=tool_name,
            step_id=step_id,
        )

__all__ = [
    "FailureFact",
    "UNKNOWN_FAILURE_CODE",
]
