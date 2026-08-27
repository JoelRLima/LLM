"""Compatibility exports for planning status/capability vocabulary.

The canonical values live in the runtime taxonomy and capability modules.
These names remain as narrow import adapters for older planning consumers.
"""

from __future__ import annotations

from agent.capabilities import WRITE_CAPABILITIES as _WRITE_CAPABILITIES
from agent.runtime.outcome_taxonomy import NON_SUCCESS_STATUSES

WRITE_CAPABILITIES = frozenset(item.value for item in _WRITE_CAPABILITIES)
TERMINAL_FAILURE_STATUSES = NON_SUCCESS_STATUSES
