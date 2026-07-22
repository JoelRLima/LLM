"""Compatibility alias for :mod:`agent.runtime.config`."""

import sys

from agent.runtime import config as _implementation

sys.modules[__name__] = _implementation
