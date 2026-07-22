"""Compatibility alias for :mod:`agent.runtime.paths`."""

import sys

from agent.runtime import paths as _implementation

sys.modules[__name__] = _implementation
