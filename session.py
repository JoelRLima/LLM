"""Compatibility alias for :mod:`agent.llm.session`."""

import sys

from agent.llm import session as _implementation

sys.modules[__name__] = _implementation
