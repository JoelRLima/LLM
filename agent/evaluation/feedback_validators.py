"""Field-level invariants for persisted human-feedback records."""

from __future__ import annotations

from typing import Any, Callable

from agent.reporting.public_safety import sanitize_public_text

_ErrorFactory = Callable[[str, str], Exception]


def validate_record_identity(
    schema_version: object,
    expected_schema_version: int,
    feedback_id: object,
    revision: object,
    supersedes_revision: object,
    invalid: _ErrorFactory,
) -> None:
    """Validate schema, identifier, and revision-chain identity fields."""
    if isinstance(schema_version, bool) or schema_version != expected_schema_version:
        raise invalid("FEEDBACK_STORE_CORRUPT", "unsupported feedback schema")
    if not isinstance(feedback_id, str) or len(feedback_id) != 35 or not feedback_id.startswith("fb-"):
        raise invalid("FEEDBACK_STORE_CORRUPT", "feedback_id is invalid")
    if any(char not in "0123456789abcdef" for char in feedback_id[3:]):
        raise invalid("FEEDBACK_STORE_CORRUPT", "feedback_id is invalid")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise invalid("FEEDBACK_STORE_CORRUPT", "feedback revision is invalid")
    if revision == 1:
        if supersedes_revision is not None:
            raise invalid("FEEDBACK_STORE_CORRUPT", "revision one cannot supersede")
    elif (
        isinstance(supersedes_revision, bool)
        or not isinstance(supersedes_revision, int)
        or supersedes_revision != revision - 1
    ):
        raise invalid("FEEDBACK_STORE_CORRUPT", "feedback revision chain is invalid")


def validate_record_content(
    target: object,
    verdict: object,
    comment: object,
    comment_redacted: object,
    created_at: object,
    *,
    target_type: type[Any],
    verdict_type: type[Any],
    max_comment_chars: int,
    invalid: _ErrorFactory,
) -> None:
    """Validate typed target/verdict, bounded comment, and timestamp fields."""
    if not isinstance(target, target_type):
        raise invalid("FEEDBACK_STORE_CORRUPT", "feedback target is invalid")
    if not isinstance(verdict, verdict_type):
        raise invalid("FEEDBACK_STORE_CORRUPT", "feedback verdict is invalid")
    if comment is not None and (
        not isinstance(comment, str) or len(comment) > max_comment_chars
    ):
        raise invalid("FEEDBACK_COMMENT_TOO_LARGE", "feedback comment exceeds 2000 characters")
    if not isinstance(comment_redacted, bool):
        raise invalid("FEEDBACK_STORE_CORRUPT", "comment_redacted is invalid")
    if not isinstance(created_at, str) or not created_at.strip():
        raise invalid("FEEDBACK_TARGET_INVALID", "created_at is required")


def normalize_comment(
    comment: str | None,
    *,
    max_comment_chars: int,
    comment_too_large_reason: str,
    invalid: _ErrorFactory,
) -> tuple[str | None, bool]:
    """Sanitize a submitted comment while retaining its redaction signal."""
    if comment is None:
        return None, False
    supplied = str(comment)
    safe = sanitize_public_text(supplied)
    if len(safe) > max_comment_chars:
        raise invalid(comment_too_large_reason, "feedback comment exceeds 2000 characters")
    return safe, safe != supplied


__all__ = ["normalize_comment", "validate_record_content", "validate_record_identity"]
