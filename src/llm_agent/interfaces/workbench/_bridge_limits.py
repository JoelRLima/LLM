"""Limits and transport behavior for the bounded Workbench bridge."""

from __future__ import annotations

MAX_REPORT_REFERENCES = 128
MAX_EVENT_TEXT = 64_000
# Admission requires an empty queue. At most two deltas (split by cancel),
# one cancel, one reference expiry, one terminal event per admitted request.
MAX_PENDING_EVENTS = 5
MAX_WAIT_EVENTS_MS = 1_000
