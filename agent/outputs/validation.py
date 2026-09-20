"""Field and cross-field validation for retained output models."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, NoReturn


def _raise(reason_code: str, message: str) -> NoReturn:
    from agent.outputs.models import OutputValidationError

    raise OutputValidationError(reason_code, message)


def _validate_optional_identifier(value: object, label: str, reason_code: str) -> None:
    from agent.outputs.models import _CONTROL

    if value is not None and (
        not isinstance(value, str) or len(value) > 200 or _CONTROL.search(value)
    ):
        _raise(reason_code, f"{label} is invalid")


def _validate_publish_request_types(request: Any) -> None:
    from agent.outputs.models import (
        _CONTROL,
        OUTPUT_REQUEST_INVALID,
        OutputContentPolicy,
        OutputKind,
        OutputSource,
    )

    if not isinstance(request.kind, OutputKind) or not isinstance(request.source, OutputSource):
        _raise(OUTPUT_REQUEST_INVALID, "kind or source is invalid")
    if not isinstance(request.content_policy, OutputContentPolicy):
        _raise(OUTPUT_REQUEST_INVALID, "content policy is invalid")
    if not isinstance(request.text, str):
        _raise(OUTPUT_REQUEST_INVALID, "text is invalid")
    if not isinstance(request.title, str) or len(request.title) > 200 or _CONTROL.search(request.title):
        _raise(OUTPUT_REQUEST_INVALID, "title is invalid")
    if not isinstance(request.source_truncated, bool) or not isinstance(request.force_artifact, bool):
        _raise(OUTPUT_REQUEST_INVALID, "request flags are invalid")


def _validate_publish_request_metadata(request: Any) -> None:
    from agent.outputs.models import _METADATA_KEYS, OUTPUT_REQUEST_INVALID

    if not isinstance(request.metadata, Mapping):
        _raise(OUTPUT_REQUEST_INVALID, "metadata is invalid")
    if set(request.metadata) - _METADATA_KEYS:
        _raise(OUTPUT_REQUEST_INVALID, "metadata contains unknown keys")
    for key, value in request.metadata.items():
        if not isinstance(key, str) or key not in _METADATA_KEYS:
            _raise(OUTPUT_REQUEST_INVALID, "metadata key is invalid")
        _validate_request_metadata_value(value, key)


def _validate_request_metadata_value(value: object, key: str) -> None:
    from agent.outputs.models import _CONTROL, OUTPUT_REQUEST_INVALID

    if isinstance(value, (dict, list, tuple, set)) or value is None:
        _raise(OUTPUT_REQUEST_INVALID, f"metadata value is invalid: {key}")
    result = str(value)
    if _CONTROL.search(result) or len(result) > 500:
        _raise(OUTPUT_REQUEST_INVALID, f"metadata value is invalid: {key}")


def _validate_publish_request_policy(request: Any) -> None:
    from agent.outputs.models import (
        OUTPUT_CONTENT_POLICY_DENIED,
        OutputContentPolicy,
        OutputKind,
        OutputSource,
    )

    if request.content_policy is not OutputContentPolicy.PRESERVE_USER_CONTENT:
        return
    allowed = (
        request.source is OutputSource.WORKSPACE_QUERY
        and request.kind in {OutputKind.FILE, OutputKind.TABLE, OutputKind.SEARCH, OutputKind.DIFF, OutputKind.TEXT}
        and str(request.metadata.get("result_status", "")).casefold() == "succeeded"
    )
    if not allowed:
        _raise(OUTPUT_CONTENT_POLICY_DENIED, "preserve_user_content requires a successful confined query")


def validate_publish_request(request: Any) -> None:
    _validate_publish_request_types(request)
    _validate_optional_identifier(request.run_id, "run_id", "OUTPUT_REQUEST_INVALID")
    _validate_optional_identifier(request.action_id, "action_id", "OUTPUT_REQUEST_INVALID")
    _validate_publish_request_metadata(request)
    _validate_publish_request_policy(request)


def _validate_artifact_identity(artifact: Any) -> None:
    from agent.outputs.models import (
        _CONTROL,
        OUTPUT_ID_INVALID,
        OUTPUT_ID_PATTERN,
        OUTPUT_MEDIA_TYPE,
        OUTPUT_METADATA_INVALID,
        OUTPUT_SCHEMA_VERSION,
        OutputKind,
        OutputSource,
    )

    if isinstance(artifact.schema_version, bool) or artifact.schema_version != OUTPUT_SCHEMA_VERSION:
        _raise(OUTPUT_METADATA_INVALID, "unsupported output schema")
    if not isinstance(artifact.output_id, str) or OUTPUT_ID_PATTERN.fullmatch(artifact.output_id) is None:
        _raise(OUTPUT_ID_INVALID, "output_id is invalid")
    if not isinstance(artifact.kind, OutputKind) or not isinstance(artifact.source, OutputSource):
        _raise(OUTPUT_METADATA_INVALID, "kind or source is invalid")
    if not isinstance(artifact.title, str) or len(artifact.title) > 200:
        _raise(OUTPUT_METADATA_INVALID, "title is invalid")
    if _CONTROL.search(artifact.title):
        _raise(OUTPUT_METADATA_INVALID, "title is invalid")
    if not isinstance(artifact.created_at, str) or not artifact.created_at:
        _raise(OUTPUT_METADATA_INVALID, "created_at is invalid")
    if artifact.media_type != OUTPUT_MEDIA_TYPE:
        _raise(OUTPUT_METADATA_INVALID, "media_type is invalid")
    if not isinstance(artifact.payload_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", artifact.payload_sha256) is None:
        _raise(OUTPUT_METADATA_INVALID, "payload hash is invalid")


def _validate_artifact_counts(artifact: Any) -> None:
    from agent.outputs.models import MAX_OUTPUT_PAYLOAD_BYTES, OUTPUT_METADATA_INVALID, OUTPUT_PAYLOAD_TOO_LARGE

    for label, value in (
        ("payload_bytes", artifact.payload_bytes),
        ("char_count", artifact.char_count),
        ("line_count", artifact.line_count),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _raise(OUTPUT_METADATA_INVALID, f"{label} is invalid")
    if artifact.payload_bytes > MAX_OUTPUT_PAYLOAD_BYTES:
        _raise(OUTPUT_PAYLOAD_TOO_LARGE, "payload exceeds the single-artifact limit")


def _validate_artifact_optional_fields(artifact: Any) -> None:
    from agent.outputs.models import OUTPUT_METADATA_INVALID

    if not isinstance(artifact.source_truncated, bool) or not isinstance(artifact.redaction_applied, bool):
        _raise(OUTPUT_METADATA_INVALID, "output flags are invalid")
    for label, value in (("run_id", artifact.run_id), ("action_id", artifact.action_id)):
        _validate_optional_identifier(value, label, OUTPUT_METADATA_INVALID)


def _validate_artifact_metadata(artifact: Any) -> dict[str, str]:
    from agent.outputs.models import _CONTROL, _METADATA_KEYS, OUTPUT_METADATA_INVALID

    if not isinstance(artifact.metadata, Mapping):
        _raise(OUTPUT_METADATA_INVALID, "metadata is invalid")
    if set(artifact.metadata) - _METADATA_KEYS:
        _raise(OUTPUT_METADATA_INVALID, "metadata keys are invalid")
    if any(
        not isinstance(key, str)
        or not isinstance(value, str)
        or _CONTROL.search(value)
        or len(value) > 500
        for key, value in artifact.metadata.items()
    ):
        _raise(OUTPUT_METADATA_INVALID, "metadata values are invalid")
    return dict(sorted(artifact.metadata.items()))


def validate_output_artifact(artifact: Any) -> dict[str, str]:
    _validate_artifact_identity(artifact)
    _validate_artifact_counts(artifact)
    _validate_artifact_optional_fields(artifact)
    return _validate_artifact_metadata(artifact)


def validate_output_reference(reference: Any) -> None:
    from agent.outputs.models import _CONTROL, OUTPUT_ID_PATTERN, OUTPUT_REQUEST_INVALID, OutputKind

    if not isinstance(reference.output_id, str) or OUTPUT_ID_PATTERN.fullmatch(reference.output_id) is None:
        _raise(OUTPUT_REQUEST_INVALID, "output reference ID is invalid")
    if not isinstance(reference.kind, OutputKind):
        _raise(OUTPUT_REQUEST_INVALID, "output reference kind is invalid")
    if not isinstance(reference.title, str) or len(reference.title) > 200 or _CONTROL.search(reference.title):
        _raise(OUTPUT_REQUEST_INVALID, "output reference title is invalid")
    for label, value in (("char_count", reference.char_count), ("line_count", reference.line_count)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _raise(OUTPUT_REQUEST_INVALID, f"output reference {label} is invalid")
    if not isinstance(reference.source_truncated, bool):
        _raise(OUTPUT_REQUEST_INVALID, "output reference flags are invalid")


def _validate_inline_publication(publication: Any) -> None:
    if publication.artifact is not None or publication.reference is not None:
        _raise("OUTPUT_REQUEST_INVALID", "inline publication cannot carry an artifact")


def _validate_artifact_publication(publication: Any) -> None:
    from agent.outputs.models import preview_text

    if publication.artifact is None or publication.reference is None:
        _raise("OUTPUT_REQUEST_INVALID", "artifact publication requires metadata and reference")
    if publication.inline_text != preview_text(publication.inline_text):
        _raise("OUTPUT_REQUEST_INVALID", "artifact preview is not bounded")
    reference = publication.reference
    artifact = publication.artifact
    if (
        reference.output_id != artifact.output_id
        or reference.kind is not artifact.kind
        or reference.char_count != artifact.char_count
        or reference.line_count != artifact.line_count
        or reference.source_truncated != artifact.source_truncated
    ):
        _raise("OUTPUT_REQUEST_INVALID", "publication reference does not match artifact")


def validate_output_publication(publication: Any) -> None:
    from agent.outputs.models import OUTPUT_REQUEST_INVALID, OutputDisposition

    if not isinstance(publication.disposition, OutputDisposition) or not isinstance(publication.inline_text, str):
        _raise(OUTPUT_REQUEST_INVALID, "publication fields are invalid")
    if publication.disposition is OutputDisposition.INLINE_ONLY:
        _validate_inline_publication(publication)
    elif publication.disposition is OutputDisposition.ARTIFACT:
        _validate_artifact_publication(publication)
    else:
        _raise(OUTPUT_REQUEST_INVALID, "publication disposition is invalid")


def validate_output_chunk(chunk: Any) -> None:
    from agent.outputs.models import OUTPUT_ID_PATTERN, OUTPUT_READ_INVALID

    if not isinstance(chunk.output_id, str) or OUTPUT_ID_PATTERN.fullmatch(chunk.output_id) is None:
        _raise(OUTPUT_READ_INVALID, "chunk output ID is invalid")
    if isinstance(chunk.offset, bool) or not isinstance(chunk.offset, int) or chunk.offset < 0:
        _raise(OUTPUT_READ_INVALID, "chunk offset is invalid")
    if not isinstance(chunk.text, str) or not isinstance(chunk.eof, bool):
        _raise(OUTPUT_READ_INVALID, "chunk fields are invalid")
    if chunk.next_offset is not None and (
        isinstance(chunk.next_offset, bool) or not isinstance(chunk.next_offset, int) or chunk.next_offset <= chunk.offset
    ):
        _raise(OUTPUT_READ_INVALID, "chunk next offset is invalid")
