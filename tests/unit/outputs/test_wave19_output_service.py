from __future__ import annotations

from pathlib import Path

import pytest

from agent.outputs.models import (
    INLINE_OUTPUT_MAX_CHARS,
    INLINE_OUTPUT_MAX_LINES,
    MAX_OUTPUT_PAYLOAD_BYTES,
    OUTPUT_PAYLOAD_TOO_LARGE,
    OutputContentPolicy,
    OutputDisposition,
    OutputKind,
    OutputPublishRequest,
    OutputSource,
    OutputValidationError,
)
from agent.outputs.service import OutputService
from agent.runtime.paths import AppPaths


def _service(tmp_path: Path) -> OutputService:
    return OutputService(AppPaths.discover(tmp_path / "home", env={}).for_workspace("workspace"))


def _request(text: str, *, force: bool = False, policy: OutputContentPolicy = OutputContentPolicy.PUBLIC_TEXT) -> OutputPublishRequest:
    return OutputPublishRequest(
        kind=OutputKind.TEXT,
        source=OutputSource.OTHER_PUBLIC if policy is OutputContentPolicy.PUBLIC_TEXT else OutputSource.WORKSPACE_QUERY,
        title="test",
        text=text,
        content_policy=policy,
        force_artifact=force,
        metadata={"result_status": "succeeded"} if policy is OutputContentPolicy.PRESERVE_USER_CONTENT else {},
    )


def test_thresholds_are_strict_and_inline_payloads_are_not_persisted(tmp_path: Path) -> None:
    service = _service(tmp_path)
    inline = service.publish(_request("x" * INLINE_OUTPUT_MAX_CHARS))
    assert inline.disposition is OutputDisposition.INLINE_ONLY
    assert inline.artifact is None
    assert service.list() == ()

    artifact = service.publish(_request("x" * (INLINE_OUTPUT_MAX_CHARS + 1)))
    assert artifact.disposition is OutputDisposition.ARTIFACT
    assert artifact.artifact is not None

    line_artifact = service.publish(_request("x\n" * (INLINE_OUTPUT_MAX_LINES + 1)))
    assert line_artifact.disposition is OutputDisposition.ARTIFACT


def test_force_artifact_and_preview_and_unicode_chunks(tmp_path: Path) -> None:
    service = _service(tmp_path)
    publication = service.publish(_request("🙂" * 100, force=True))
    assert publication.disposition is OutputDisposition.ARTIFACT
    assert publication.artifact is not None
    assert publication.inline_text == "🙂" * 100
    chunk = service.read_chunk(publication.artifact.output_id, offset=1, limit=2)
    assert chunk.text == "🙂🙂"
    assert chunk.next_offset == 3
    assert chunk.eof is False


def test_public_redaction_happens_before_artifact_hash_and_preserve_is_exact(tmp_path: Path) -> None:
    service = _service(tmp_path)
    public = service.publish(_request("token=secret", force=True))
    assert public.artifact is not None
    assert public.artifact.redaction_applied is True
    assert "token=[REDACTED]" in service.store.read(public.artifact.output_id)

    preserved = service.publish(
        _request(
            "token=secret",
            force=True,
            policy=OutputContentPolicy.PRESERVE_USER_CONTENT,
        )
    )
    assert preserved.artifact is not None
    assert service.store.read(preserved.artifact.output_id) == "token=secret"


def test_single_payload_over_four_mib_is_rejected_without_final_files(tmp_path: Path) -> None:
    service = _service(tmp_path)
    text = "🙂" * (MAX_OUTPUT_PAYLOAD_BYTES // len("🙂".encode("utf-8")) + 1)
    with pytest.raises(OutputValidationError) as error:
        service.publish(_request(text, force=True))
    assert error.value.reason_code == OUTPUT_PAYLOAD_TOO_LARGE
    assert service.list() == ()


def test_read_bounds_reject_boolean_and_over_max_limit(tmp_path: Path) -> None:
    service = _service(tmp_path)
    publication = service.publish(_request("x", force=True))
    assert publication.artifact is not None
    with pytest.raises(OutputValidationError):
        service.read_chunk(publication.artifact.output_id, offset=False)  # type: ignore[arg-type]
    with pytest.raises(OutputValidationError):
        service.read_chunk(publication.artifact.output_id, limit=32_769)
