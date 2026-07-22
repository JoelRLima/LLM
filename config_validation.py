"""Compatibility alias for :mod:`agent.runtime.config_validation`."""

import sys

from agent.runtime import config_validation as _implementation

sys.modules[__name__] = _implementation
