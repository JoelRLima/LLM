from __future__ import annotations

import pytest

from agent.outputs.models import (
    OUTPUT_CONTENT_POLICY_DENIED,
    OutputContentPolicy,
    OutputKind,
    OutputPublishRequest,
    OutputSource,
    OutputValidationError,
    line_count,
    payload_digest,
    preview_text,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    (("", 0), ("one", 1), ("one\n", 1), ("one\ntwo", 2), ("one\r\ntwo\r\n", 2)),
)
def test_line_count_is_the_frozen_logical_line_algorithm(text: str, expected: int) -> None:
    assert line_count(text) == expected


def test_payload_digest_counts_utf8_bytes_and_unicode_chars_separately() -> None:
    digest, byte_count, char_count, lines = payload_digest("á\n🙂")
    assert len(digest) == 64
    assert byte_count == len("á\n🙂".encode("utf-8"))
    assert char_count == 3
    assert lines == 2


def test_preview_admits_at_most_forty_lines_and_two_thousand_chars() -> None:
    preview = preview_text("".join(f"{index:03d}\n" for index in range(100)))
    assert len(preview) <= 2_000
    assert len(preview.splitlines()) <= 40


def test_preserve_user_content_is_confined_to_successful_workspace_queries() -> None:
    request = OutputPublishRequest(
        kind=OutputKind.FILE,
        source=OutputSource.WORKSPACE_QUERY,
        title="read",
        text="token=abc",
        content_policy=OutputContentPolicy.PRESERVE_USER_CONTENT,
        metadata={"result_status": "succeeded"},
    )
    assert request.content_policy is OutputContentPolicy.PRESERVE_USER_CONTENT
    with pytest.raises(OutputValidationError) as error:
        OutputPublishRequest(
            kind=OutputKind.LOG,
            source=OutputSource.WORKER_DIAGNOSTIC,
            title="diagnostic",
            text="token=abc",
            content_policy=OutputContentPolicy.PRESERVE_USER_CONTENT,
            metadata={"result_status": "succeeded"},
        )
    assert error.value.reason_code == OUTPUT_CONTENT_POLICY_DENIED
