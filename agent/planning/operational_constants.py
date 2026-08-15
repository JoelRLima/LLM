"""Shared completion status/capability constants."""

from __future__ import annotations

WRITE_CAPABILITIES = frozenset({"write", "vcs_write"})
TERMINAL_FAILURE_STATUSES = frozenset(
    {
        "blocked",
        "cancelled",
        "failed",
        "permission_denied",
        "protocol_error",
        "timed_out",
        "unavailable",
    }
)
