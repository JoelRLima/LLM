# ruff: noqa: E402, I001
"""Compatibility import for prototype tools; implementation lives in the package."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "src"))

from llm_agent.interfaces.workbench import webview as _webview  # noqa: E402

MAX_EVENT_TEXT = _webview.MAX_EVENT_TEXT
MAX_PENDING_EVENTS = _webview.MAX_PENDING_EVENTS
MAX_REPORT_REFERENCES = _webview.MAX_REPORT_REFERENCES
MAX_WAIT_EVENTS_MS = _webview.MAX_WAIT_EVENTS_MS
SINGLE_INSTANCE_MUTEX = _webview.SINGLE_INSTANCE_MUTEX
WorkbenchBridge = _webview.WorkbenchBridge
_acquire_single_instance = _webview._acquire_single_instance
_release_single_instance = _webview._release_single_instance
create_session = _webview.create_session
main = _webview.main
parse_arguments = _webview.parse_arguments

__all__ = [
    "MAX_EVENT_TEXT",
    "MAX_PENDING_EVENTS",
    "MAX_REPORT_REFERENCES",
    "MAX_WAIT_EVENTS_MS",
    "SINGLE_INSTANCE_MUTEX",
    "WorkbenchBridge",
    "create_session",
    "main",
    "parse_arguments",
]


if __name__ == "__main__":
    main()
