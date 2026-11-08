"""Redact the fully rendered log record, including exception tracebacks."""
from __future__ import annotations

import logging
from copy import copy
from typing import cast

from llm_agent.agent.observability.redaction import redact_text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Formatting first covers %-arguments, exception text and stack_info;
        # never mutate the record shared with other handlers.
        rendered = super().format(copy(record))
        return cast(str, redact_text(rendered, limit=len(rendered)))
