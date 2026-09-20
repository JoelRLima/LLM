"""UI-neutral output publication and bounded reads."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from agent.outputs.models import (
    INLINE_OUTPUT_MAX_CHARS,
    INLINE_OUTPUT_MAX_LINES,
    MAX_OUTPUT_PAYLOAD_BYTES,
    OUTPUT_MEDIA_TYPE,
    OUTPUT_PAYLOAD_TOO_LARGE,
    OUTPUT_READ_MAX_CHARS,
    OUTPUT_REQUEST_INVALID,
    OutputArtifact,
    OutputChunk,
    OutputContentPolicy,
    OutputDisposition,
    OutputPublication,
    OutputPublishRequest,
    OutputReference,
    OutputValidationError,
    payload_digest,
    preview_text,
)
from agent.outputs.store import OutputStore
from agent.reporting.public_safety import sanitize_public_text
from agent.runtime.paths import WorkspacePaths


def _default_clock() -> str:
    return datetime.now(timezone.utc).isoformat()


class OutputService:
    def __init__(
        self,
        workspace_paths: WorkspacePaths,
        *,
        clock: Callable[[], str | datetime] = _default_clock,
    ) -> None:
        self.workspace_paths = workspace_paths
        self.clock = clock
        self.store = OutputStore(workspace_paths.output_artifacts_dir, clock=clock)

    def _normalized(self, request: OutputPublishRequest) -> tuple[str, bool, str, dict[str, str]]:
        if not isinstance(request, OutputPublishRequest):
            raise OutputValidationError(OUTPUT_REQUEST_INVALID, "output request is invalid")
        if request.content_policy is OutputContentPolicy.PUBLIC_TEXT:
            text = sanitize_public_text(request.text)
            redacted = text != request.text
        else:
            text = request.text
            redacted = False
        metadata = {}
        for key, value in request.metadata.items():
            normalized = sanitize_public_text(str(value))
            if len(normalized) > 500:
                raise OutputValidationError(OUTPUT_REQUEST_INVALID, f"metadata value is invalid: {key}")
            metadata[key] = normalized
        title = sanitize_public_text(request.title)
        if len(title) > 200:
            raise OutputValidationError(OUTPUT_REQUEST_INVALID, "title exceeds 200 characters")
        return text, redacted, title, metadata

    def publish(self, request: OutputPublishRequest) -> OutputPublication:
        text, redaction_applied, title, metadata = self._normalized(request)
        digest, byte_count, chars, lines = payload_digest(text)
        inline = not request.force_artifact and chars <= INLINE_OUTPUT_MAX_CHARS and lines <= INLINE_OUTPUT_MAX_LINES
        if inline:
            return OutputPublication(OutputDisposition.INLINE_ONLY, text, None, None)
        if byte_count > MAX_OUTPUT_PAYLOAD_BYTES:
            raise OutputValidationError(OUTPUT_PAYLOAD_TOO_LARGE, "output payload exceeds its bound")
        output_id = "out-" + uuid4().hex
        artifact = OutputArtifact(
            schema_version=1,
            output_id=output_id,
            kind=request.kind,
            source=request.source,
            title=title,
            created_at=self._created_at(),
            media_type=OUTPUT_MEDIA_TYPE,
            payload_sha256=digest,
            payload_bytes=byte_count,
            char_count=chars,
            line_count=lines,
            source_truncated=request.source_truncated,
            redaction_applied=redaction_applied,
            run_id=sanitize_public_text(request.run_id) if request.run_id is not None else None,
            action_id=sanitize_public_text(request.action_id) if request.action_id is not None else None,
            metadata=metadata,
        )
        committed = self.store.commit(artifact, text)
        reference = OutputReference(
            output_id=committed.output_id,
            kind=committed.kind,
            title=committed.title,
            char_count=committed.char_count,
            line_count=committed.line_count,
            source_truncated=committed.source_truncated,
        )
        return OutputPublication(
            OutputDisposition.ARTIFACT,
            preview_text(text),
            committed,
            reference,
        )

    def _created_at(self) -> str:
        value = self.clock()
        if isinstance(value, datetime):
            return value.isoformat()
        if not isinstance(value, str) or not value:
            raise OutputValidationError(OUTPUT_REQUEST_INVALID, "output clock returned an invalid timestamp")
        return value

    @staticmethod
    def _read_bounds(offset: int, limit: int) -> None:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise OutputValidationError("OUTPUT_READ_INVALID", "offset is invalid")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= OUTPUT_READ_MAX_CHARS:
            raise OutputValidationError("OUTPUT_READ_INVALID", "limit is invalid")

    def read_chunk(self, output_id: str, *, offset: int = 0, limit: int = 16_384) -> OutputChunk:
        self._read_bounds(offset, limit)
        artifact = self.store.metadata(output_id)
        text = self.store.read(artifact.output_id)
        if offset > len(text):
            return OutputChunk(artifact.output_id, offset, "", None, True)
        end = min(len(text), offset + limit)
        chunk = text[offset:end]
        eof = end >= len(text)
        return OutputChunk(artifact.output_id, offset, chunk, None if eof else end, eof)

    def metadata(self, output_id: str) -> OutputArtifact:
        return self.store.metadata(output_id)

    def list(self, *, limit: int = 50) -> tuple[OutputArtifact, ...]:
        return self.store.list(limit=limit)


__all__ = ["OutputService"]
