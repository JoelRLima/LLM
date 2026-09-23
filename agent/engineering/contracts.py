"""Closed neutral contracts for the Engineering control plane."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from agent.runtime.paths import AppPaths

ENGINEERING_SCHEMA_VERSION = 1
MAX_ENGINEERING_OPERATION_ID_BYTES = 128
MAX_ENGINEERING_PARAMETERS_BYTES = 16384
MAX_ENGINEERING_ERROR_MESSAGE_BYTES = 512
MAX_ENGINEERING_REFERENCES = 64
_OPERATION_ID = re.compile('^[a-z0-9]+(?:[.-][a-z0-9]+)*$')
_RUN_ID = re.compile('^engr-[0-9a-f]{32}$')
_WORKSPACE_ID = re.compile('^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
_CANDIDATE_IDENTITY = re.compile('^[0-9a-f]{40}:[0-9a-f]{64}:[0-9a-f]{64}$')
_SHA256 = re.compile('^[0-9a-f]{64}$')
_UTC_TIMESTAMP = re.compile('^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}\\.\\d{6}Z$')
class EngineeringRunPhase(str, Enum):
    ACTIVE = 'active'
    TERMINAL = 'terminal'
class EngineeringTerminalStatus(str, Enum):
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    UNVERIFIED = 'unverified'
    PROTOCOL_ERROR = 'protocol_error'
class EngineeringQueryStatus(str, Enum):
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    BLOCKED = 'blocked'
    UNVERIFIED = 'unverified'
    PROTOCOL_ERROR = 'protocol_error'
class EngineeringBackendStatus(str, Enum):
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
class EngineeringScope(str, Enum):
    GLOBAL = 'global'
    WORKSPACE = 'workspace'
    SOURCE_REPOSITORY = 'source_repository'
class EngineeringCaller(str, Enum):
    HUMAN_INTERACTIVE = 'human_interactive'
    AUTOMATION_HEADLESS = 'automation_headless'
    MODEL_SAFE_AGENT = 'model_safe_agent'
    MCP = 'mcp'
class EngineeringPermission(str, Enum):
    RUN_EXTERNAL = 'run_external'
    USE_NETWORK = 'use_network'
    USE_REAL_MODEL = 'use_real_model'
    INJECT_FAULT = 'inject_fault'
class EngineeringTransitionMode(str, Enum):
    MANAGED_EXECUTION_GUARD = 'managed_execution_guard'
    TERMINAL_PENDING = 'terminal_pending'
ENGINEERING_REASON_CODES = ('ENGINEERING_OPERATION_NOT_FOUND', 'ENGINEERING_OPERATION_UNAVAILABLE', 'ENGINEERING_PERMISSION_REQUIRED', 'ENGINEERING_EXTERNAL_NOT_AUTHORIZED', 'ENGINEERING_NETWORK_NOT_AUTHORIZED', 'ENGINEERING_REAL_MODEL_NOT_AUTHORIZED', 'ENGINEERING_FAULT_INJECTION_NOT_AUTHORIZED', 'ENGINEERING_FAULT_INVALID', 'ENGINEERING_WORKSPACE_REQUIRED', 'ENGINEERING_SOURCE_REPOSITORY_REQUIRED', 'ENGINEERING_INVALID_PARAMETERS', 'ENGINEERING_PARAMETERS_TOO_LARGE', 'ENGINEERING_RUN_NOT_FOUND', 'ENGINEERING_RUN_ACTIVE', 'ENGINEERING_OWNER_LIVENESS_INDETERMINATE', 'ENGINEERING_OWNER_DEAD', 'ENGINEERING_RUN_RECORD_CORRUPT', 'ENGINEERING_STORE_STATE_UNSAFE', 'ENGINEERING_STORE_CAPACITY_EXCEEDED', 'ENGINEERING_LOCK_BUSY', 'ENGINEERING_BACKEND_STATE_INDETERMINATE', 'ENGINEERING_RESULT_PERSIST_FAILED', 'ENGINEERING_DURABLE_STATE_INDETERMINATE', 'ENGINEERING_BACKEND_RESULT_INVALID', 'ENGINEERING_BACKEND_FAILED', 'ENGINEERING_CANCELLED', 'ENGINEERING_DEADLINE_EXCEEDED', 'ENGINEERING_INTERNAL_ERROR')
_REASON_ORDER = {value: index for index, value in enumerate(ENGINEERING_REASON_CODES)}
ERROR_MESSAGES: Mapping[str, str] = {code: message for code, message in (('ENGINEERING_OPERATION_NOT_FOUND', 'The Engineering operation is not known.'), ('ENGINEERING_OPERATION_UNAVAILABLE', 'The Engineering operation is unavailable.'), ('ENGINEERING_PERMISSION_REQUIRED', 'An Engineering permission is required.'), ('ENGINEERING_EXTERNAL_NOT_AUTHORIZED', 'External execution was not authorized.'), ('ENGINEERING_NETWORK_NOT_AUTHORIZED', 'Network use was not authorized.'), ('ENGINEERING_REAL_MODEL_NOT_AUTHORIZED', 'Real-model use was not authorized.'), ('ENGINEERING_FAULT_INJECTION_NOT_AUTHORIZED', 'Fault injection was not authorized.'), ('ENGINEERING_FAULT_INVALID', 'The Engineering fault plan is invalid.'), ('ENGINEERING_WORKSPACE_REQUIRED', 'A trusted workspace context is required.'), ('ENGINEERING_SOURCE_REPOSITORY_REQUIRED', 'A trusted source repository is required.'), ('ENGINEERING_INVALID_PARAMETERS', 'The Engineering parameters are invalid.'), ('ENGINEERING_PARAMETERS_TOO_LARGE', 'The Engineering parameters are too large.'), ('ENGINEERING_RUN_NOT_FOUND', 'The Engineering run was not found.'), ('ENGINEERING_RUN_ACTIVE', 'The Engineering run is active.'), ('ENGINEERING_OWNER_LIVENESS_INDETERMINATE', 'The run owner liveness is indeterminate.'), ('ENGINEERING_OWNER_DEAD', 'The run owner is dead.'), ('ENGINEERING_RUN_RECORD_CORRUPT', 'The Engineering run record is corrupt.'), ('ENGINEERING_STORE_STATE_UNSAFE', 'The Engineering store state is unsafe.'), ('ENGINEERING_STORE_CAPACITY_EXCEEDED', 'The Engineering store capacity is exceeded.'), ('ENGINEERING_LOCK_BUSY', 'The Engineering state is busy.'), ('ENGINEERING_BACKEND_STATE_INDETERMINATE', 'The backend state is indeterminate.'), ('ENGINEERING_RESULT_PERSIST_FAILED', 'The Engineering result could not be persisted.'), ('ENGINEERING_DURABLE_STATE_INDETERMINATE', 'The durable Engineering state is indeterminate.'), ('ENGINEERING_BACKEND_RESULT_INVALID', 'The backend result is invalid.'), ('ENGINEERING_BACKEND_FAILED', 'The Engineering backend failed.'), ('ENGINEERING_CANCELLED', 'The Engineering run was cancelled.'), ('ENGINEERING_DEADLINE_EXCEEDED', 'The Engineering deadline was exceeded.'), ('ENGINEERING_INTERNAL_ERROR', 'An internal Engineering error occurred.'))}
def validate_operation_id(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value.encode('utf-8')) > MAX_ENGINEERING_OPERATION_ID_BYTES or (not _OPERATION_ID.fullmatch(value)):
        raise ValueError('invalid operation id')
    return value
def validate_run_id(value: object) -> str:
    if not isinstance(value, str) or not _RUN_ID.fullmatch(value):
        raise ValueError('invalid run id')
    return value
def validate_workspace_id(value: object) -> str:
    if not isinstance(value, str) or len(value.encode('utf-8')) > 128 or (not _WORKSPACE_ID.fullmatch(value)):
        raise ValueError('invalid workspace id')
    return value
def validate_candidate_identity(value: object) -> str:
    if not isinstance(value, str) or len(value) != 170 or (not _CANDIDATE_IDENTITY.fullmatch(value)):
        raise ValueError('invalid candidate identity')
    return value
def validate_sha256(value: object, *, nullable: bool=False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError('invalid sha256')
    return value
def canonical_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError('utc_now must return timezone-aware UTC')
    return value.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
def parse_canonical_utc(value: object) -> datetime:
    if not isinstance(value, str) or not _UTC_TIMESTAMP.fullmatch(value):
        raise ValueError('invalid UTC timestamp')
    parsed = datetime.strptime(value, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc)
    if canonical_utc(parsed) != value:
        raise ValueError('invalid UTC timestamp')
    return parsed
def order_reason_codes(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    unique = set(values)
    if any((value not in _REASON_ORDER for value in unique)):
        raise ValueError('unknown Engineering reason code')
    return tuple(sorted(unique, key=_REASON_ORDER.__getitem__))
def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
def _bounded_text(value: object, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value.encode('utf-8')) > limit:
        raise ValueError('invalid bounded text')
    return value
@dataclass(frozen=True)
class EngineeringEffects:
    network: bool
    real_model: bool
    managed_external_process: bool
    mutating_or_executing: bool
    hermetic: bool
    def __post_init__(self) -> None:
        if not all((isinstance(value, bool) for value in self.to_dict().values())):
            raise TypeError('effect declarations must be bool')
        if self.hermetic and (self.network or self.real_model):
            raise ValueError('invalid hermetic effects')
    def to_dict(self) -> dict[str, bool]:
        return {'network': self.network, 'real_model': self.real_model, 'managed_external_process': self.managed_external_process, 'mutating_or_executing': self.mutating_or_executing, 'hermetic': self.hermetic}
@dataclass(frozen=True)
class EngineeringOperationDescriptor:
    operation_id: str
    scope: EngineeringScope
    effects: EngineeringEffects
    parameter_schema: Mapping[str, Any]
    model_safe: bool = False
    def __post_init__(self) -> None:
        validate_operation_id(self.operation_id)
        if not isinstance(self.scope, EngineeringScope) or not isinstance(self.parameter_schema, Mapping):
            raise TypeError('invalid descriptor')
        if not isinstance(self.model_safe, bool):
            raise TypeError('model_safe must be bool')
@dataclass(frozen=True)
class EngineeringRequest:
    operation_id: str
    parameters: Mapping[str, Any]
@dataclass(frozen=True)
class EngineeringWorkspaceContext:
    workspace_id: str
    root: Path
    def __post_init__(self) -> None:
        validate_workspace_id(self.workspace_id)
@dataclass(frozen=True)
class SourceRepositoryContext:
    root: Path
    candidate_identity: str
    def __post_init__(self) -> None:
        validate_candidate_identity(self.candidate_identity)
class SupportsIsSet(Protocol):
    def is_set(self) -> bool:
        ...
@dataclass(frozen=True)
class EngineeringExecutionContext:
    caller: EngineeringCaller
    app_paths: AppPaths
    workspace: EngineeringWorkspaceContext | None
    source_repository: SourceRepositoryContext | None
    permissions: frozenset[EngineeringPermission]
    utc_now: Callable[[], datetime]
    monotonic_now: Callable[[], float]
    deadline_monotonic: float | None = None
    cancellation_token: SupportsIsSet | None = None
    fault_plan: Any | None = None
@dataclass(frozen=True)
class EngineeringEnvironmentV1:
    workspace_id: str | None
    candidate_identity: str | None
    schema_version: int = ENGINEERING_SCHEMA_VERSION
    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError('invalid schema version')
        if self.workspace_id is not None:
            validate_workspace_id(self.workspace_id)
        if self.candidate_identity is not None:
            validate_candidate_identity(self.candidate_identity)
    def to_dict(self) -> dict[str, Any]:
        return {'schema_version': 1, 'workspace_id': self.workspace_id, 'candidate_identity': self.candidate_identity}
@dataclass(frozen=True)
class EngineeringReferenceV1:
    owner: str
    kind: str
    reference_id: str
    sha256: str | None = None
    label: str | None = None
    def __post_init__(self) -> None:
        _bounded_text(self.owner, 64)
        _bounded_text(self.kind, 64)
        _bounded_text(self.reference_id, 512)
        if self.label is not None:
            _bounded_text(self.label, 256)
        validate_sha256(self.sha256, nullable=True)
    def to_dict(self) -> dict[str, Any]:
        return {'owner': self.owner, 'kind': self.kind, 'reference_id': self.reference_id, 'sha256': self.sha256, 'label': self.label}
@dataclass(frozen=True)
class EngineeringBackendOutcome:
    status: EngineeringBackendStatus
    summary: Mapping[str, Any]
    references: tuple[EngineeringReferenceV1, ...]
    live_model_used: bool
    network_used: bool
class EngineeringBackend(Protocol):
    def execute(self, request: EngineeringRequest, context: EngineeringExecutionContext) -> EngineeringBackendOutcome:
        ...
class EngineeringBackendProtocolError(RuntimeError):
    pass
class EngineeringBackendStateIndeterminateError(RuntimeError):
    pass


class EngineeringStoreError(RuntimeError):
    def __init__(self, code: str, *, candidate_run_id: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.candidate_run_id = candidate_run_id
@dataclass(frozen=True)
class EngineeringOperationViewV1:
    operation_id: str
    scope: EngineeringScope
    effects: EngineeringEffects
    available: bool
    unavailable_reason: str | None
    parameter_schema: Mapping[str, Any]
    schema_version: int = 1
    model_safe: bool = False
    def to_dict(self) -> dict[str, Any]:
        # Keep the W20-A public projection stable; model-safe adapters inspect
        # this trusted typed field without widening the legacy JSON surface.
        return {'schema_version': self.schema_version, 'operation_id': self.operation_id, 'scope': self.scope.value, 'effects': self.effects.to_dict(), 'available': self.available, 'unavailable_reason': self.unavailable_reason, 'parameter_schema': _plain_json(self.parameter_schema)}
@dataclass(frozen=True)
class EngineeringRunSummaryV1:
    run_id: str
    operation_id: str
    phase: EngineeringRunPhase
    status: EngineeringTerminalStatus | None
    reason_codes: tuple[str, ...]
    started_at_utc: str
    finished_at_utc: str | None
    duration_ms: int | None
    workspace_id: str | None
    persisted: bool
    schema_version: int = 1
    def __post_init__(self) -> None:
        validate_run_id(self.run_id)
        validate_operation_id(self.operation_id)
        parse_canonical_utc(self.started_at_utc)
        if self.workspace_id is not None:
            validate_workspace_id(self.workspace_id)
        if self.phase is EngineeringRunPhase.ACTIVE:
            if self.status is not None or self.finished_at_utc is not None or self.duration_ms is not None:
                raise ValueError('invalid ACTIVE summary')
        elif self.status is None or self.finished_at_utc is None:
            raise ValueError('invalid TERMINAL summary')
        else:
            parse_canonical_utc(self.finished_at_utc)
            if self.duration_ms is not None and (not isinstance(self.duration_ms, int) or self.duration_ms < 0):
                raise ValueError('invalid duration')
        object.__setattr__(self, 'reason_codes', order_reason_codes(self.reason_codes))
    def to_dict(self) -> dict[str, Any]:
        return {'schema_version': self.schema_version, 'run_id': self.run_id, 'operation_id': self.operation_id, 'phase': self.phase.value, 'status': None if self.status is None else self.status.value, 'reason_codes': list(self.reason_codes), 'started_at_utc': self.started_at_utc, 'finished_at_utc': self.finished_at_utc, 'duration_ms': self.duration_ms, 'workspace_id': self.workspace_id, 'persisted': self.persisted}
@dataclass(frozen=True)
class EngineeringRunResultV1(EngineeringRunSummaryV1):
    summary: Mapping[str, Any] | None = None
    references: tuple[EngineeringReferenceV1, ...] = ()
    environment: EngineeringEnvironmentV1 | None = None
    def __post_init__(self) -> None:
        super().__post_init__()
        if self.phase is not EngineeringRunPhase.TERMINAL or self.environment is None or self.summary is None:
            raise ValueError('run result must be terminal')
    def to_dict(self) -> dict[str, Any]:
        document = super().to_dict()
        document.update({'summary': _plain_json(self.summary), 'references': [item.to_dict() for item in self.references], 'environment': self.environment.to_dict() if self.environment else None})
        return document
@dataclass(frozen=True)
class EngineeringErrorV1:
    query_status: EngineeringQueryStatus
    code: str
    run_id: str | None = None
    run: EngineeringRunSummaryV1 | None = None
    schema_version: int = 1
    def __post_init__(self) -> None:
        if self.code not in _REASON_ORDER:
            raise ValueError('unknown Engineering reason code')
        if self.run_id is not None:
            validate_run_id(self.run_id)
    @property
    def message(self) -> str:
        return ERROR_MESSAGES[self.code]
    def to_dict(self) -> dict[str, Any]:
        return {'schema_version': self.schema_version, 'query_status': self.query_status.value, 'code': self.code, 'message': self.message, 'run_id': self.run_id, 'run': None if self.run is None else self.run.to_dict()}
EngineeringExecutionResponseV1 = EngineeringRunResultV1 | EngineeringErrorV1
def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    if isinstance(value, list):
        return [_plain_json(item) for item in value]
    return value
