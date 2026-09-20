"""Typed contracts for retained display output."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from agent.outputs.validation import (
    validate_output_artifact,
    validate_output_chunk,
    validate_output_publication,
    validate_output_reference,
    validate_publish_request,
)

OUTPUT_SCHEMA_VERSION = 1
INLINE_OUTPUT_MAX_CHARS = 8_000
INLINE_OUTPUT_MAX_LINES = 120
OUTPUT_PREVIEW_MAX_CHARS = 2_000
OUTPUT_PREVIEW_MAX_LINES = 40
OUTPUT_READ_MAX_CHARS = 32_768
MAX_OUTPUT_PAYLOAD_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_ARTIFACTS = 128
MAX_OUTPUT_STORE_BYTES = 32 * 1024 * 1024
OUTPUT_MEDIA_TYPE = "text/plain; charset=utf-8"
OUTPUT_ID_PATTERN = re.compile(r"^out-[0-9a-f]{32}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_METADATA_KEYS = frozenset({"query_kind", "file_path", "result_status", "reason_code", "source_label"})


class OutputKind(str, Enum):
    TEXT = "text"
    FILE = "file"
    TABLE = "table"
    SEARCH = "search"
    DIFF = "diff"
    LOG = "log"
    TEST = "test"
    DEBUG = "debug"
    JSON = "json"


class OutputSource(str, Enum):
    WORKSPACE_QUERY = "workspace_query"
    WEB_SEARCH = "web_search"
    CODE_WORKFLOW = "code_workflow"
    WORKER_DIAGNOSTIC = "worker_diagnostic"
    INSPECTION_EXPORT = "inspection_export"
    OTHER_PUBLIC = "other_public"


class OutputContentPolicy(str, Enum):
    PUBLIC_TEXT = "public_text"
    PRESERVE_USER_CONTENT = "preserve_user_content"


class OutputDisposition(str, Enum):
    INLINE_ONLY = "inline_only"
    ARTIFACT = "artifact"


class OutputError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


class OutputValidationError(OutputError):
    pass


class OutputStoreError(OutputError):
    pass


class OutputNotFound(OutputError):
    pass


OUTPUT_REQUEST_INVALID = "OUTPUT_REQUEST_INVALID"
OUTPUT_PAYLOAD_TOO_LARGE = "OUTPUT_PAYLOAD_TOO_LARGE"
OUTPUT_CONTENT_POLICY_DENIED = "OUTPUT_CONTENT_POLICY_DENIED"
OUTPUT_ID_INVALID = "OUTPUT_ID_INVALID"
OUTPUT_NOT_FOUND = "OUTPUT_NOT_FOUND"
OUTPUT_METADATA_INVALID = "OUTPUT_METADATA_INVALID"
OUTPUT_PAYLOAD_MISSING = "OUTPUT_PAYLOAD_MISSING"
OUTPUT_PAYLOAD_CORRUPT = "OUTPUT_PAYLOAD_CORRUPT"
OUTPUT_STORE_UNSAFE = "OUTPUT_STORE_UNSAFE"
OUTPUT_STORE_BUSY = "OUTPUT_STORE_BUSY"
OUTPUT_STORE_LIMIT = "OUTPUT_STORE_LIMIT"
OUTPUT_PERSIST_FAILED = "OUTPUT_PERSIST_FAILED"
OUTPUT_READ_INVALID = "OUTPUT_READ_INVALID"


def line_count(text: str) -> int:
    return 0 if text == "" else len(text.splitlines()) or 1


def preview_text(text: str) -> str:
    return "".join(text.splitlines(keepends=True)[:OUTPUT_PREVIEW_MAX_LINES])[:OUTPUT_PREVIEW_MAX_CHARS]


@dataclass(frozen=True, slots=True)
class OutputPublishRequest:
    kind: OutputKind
    source: OutputSource
    title: str
    text: str
    content_policy: OutputContentPolicy
    source_truncated: bool = False
    force_artifact: bool = False
    run_id: str | None = None
    action_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_publish_request(self)


@dataclass(frozen=True, slots=True)
class OutputArtifact:
    schema_version: int
    output_id: str
    kind: OutputKind
    source: OutputSource
    title: str
    created_at: str
    media_type: str
    payload_sha256: str
    payload_bytes: int
    char_count: int
    line_count: int
    source_truncated: bool
    redaction_applied: bool
    run_id: str | None
    action_id: str | None
    metadata: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", validate_output_artifact(self))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "output_id": self.output_id,
            "kind": self.kind.value,
            "source": self.source.value,
            "title": self.title,
            "created_at": self.created_at,
            "media_type": self.media_type,
            "payload_sha256": self.payload_sha256,
            "payload_bytes": self.payload_bytes,
            "char_count": self.char_count,
            "line_count": self.line_count,
            "source_truncated": self.source_truncated,
            "redaction_applied": self.redaction_applied,
            "run_id": self.run_id,
            "action_id": self.action_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: object) -> "OutputArtifact":
        if not isinstance(value, Mapping):
            raise OutputValidationError(OUTPUT_METADATA_INVALID, "metadata must be an object")
        expected = {
            "schema_version", "output_id", "kind", "source", "title", "created_at", "media_type",
            "payload_sha256", "payload_bytes", "char_count", "line_count", "source_truncated",
            "redaction_applied", "run_id", "action_id", "metadata",
        }
        if set(value) != expected:
            raise OutputValidationError(OUTPUT_METADATA_INVALID, "metadata keys are invalid")
        try:
            return cls(
                schema_version=value["schema_version"],
                output_id=value["output_id"],
                kind=OutputKind(value["kind"]),
                source=OutputSource(value["source"]),
                title=value["title"],
                created_at=value["created_at"],
                media_type=value["media_type"],
                payload_sha256=value["payload_sha256"],
                payload_bytes=value["payload_bytes"],
                char_count=value["char_count"],
                line_count=value["line_count"],
                source_truncated=value["source_truncated"],
                redaction_applied=value["redaction_applied"],
                run_id=value["run_id"],
                action_id=value["action_id"],
                metadata=value["metadata"],
            )
        except OutputError:
            raise
        except Exception as exc:
            raise OutputValidationError(OUTPUT_METADATA_INVALID, "metadata is invalid") from exc


@dataclass(frozen=True, slots=True)
class OutputReference:
    output_id: str
    kind: OutputKind
    title: str
    char_count: int
    line_count: int
    source_truncated: bool

    def __post_init__(self) -> None:
        validate_output_reference(self)


@dataclass(frozen=True, slots=True)
class OutputPublication:
    disposition: OutputDisposition
    inline_text: str
    artifact: OutputArtifact | None
    reference: OutputReference | None

    def __post_init__(self) -> None:
        validate_output_publication(self)


@dataclass(frozen=True, slots=True)
class OutputChunk:
    output_id: str
    offset: int
    text: str
    next_offset: int | None
    eof: bool

    def __post_init__(self) -> None:
        validate_output_chunk(self)


def payload_digest(text: str) -> tuple[str, int, int, int]:
    encoded = text.encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), len(encoded), len(text), line_count(text)


__all__ = [
    "INLINE_OUTPUT_MAX_CHARS", "INLINE_OUTPUT_MAX_LINES", "MAX_OUTPUT_ARTIFACTS",
    "MAX_OUTPUT_PAYLOAD_BYTES", "MAX_OUTPUT_STORE_BYTES", "OUTPUT_CONTENT_POLICY_DENIED",
    "OUTPUT_ID_INVALID", "OUTPUT_ID_PATTERN", "OUTPUT_MEDIA_TYPE", "OUTPUT_NOT_FOUND", "OUTPUT_PAYLOAD_CORRUPT",
    "OUTPUT_PAYLOAD_MISSING", "OUTPUT_PAYLOAD_TOO_LARGE", "OUTPUT_PERSIST_FAILED",
    "OUTPUT_PREVIEW_MAX_CHARS", "OUTPUT_PREVIEW_MAX_LINES", "OUTPUT_READ_MAX_CHARS",
    "OUTPUT_REQUEST_INVALID", "OUTPUT_SCHEMA_VERSION", "OUTPUT_STORE_BUSY", "OUTPUT_STORE_LIMIT",
    "OUTPUT_STORE_UNSAFE", "OUTPUT_METADATA_INVALID", "OUTPUT_READ_INVALID",
    "OutputArtifact", "OutputChunk", "OutputContentPolicy", "OutputDisposition", "OutputError",
    "OutputKind", "OutputNotFound", "OutputPublishRequest", "OutputPublication", "OutputReference",
    "OutputSource", "OutputStoreError", "OutputValidationError", "line_count", "payload_digest",
    "preview_text",
]
