"""Compatibility alias for :mod:`agent.runtime.logging`."""

import sys

from agent.runtime import logging as _implementation

sys.modules[__name__] = _implementation
